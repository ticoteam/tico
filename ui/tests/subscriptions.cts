// Offline regression for company subscriptions given to computers (backend/model_subscriptions.py): a subscription is a
// company's saved plan login or API key; a computer holds at most one per AI tool; a bot never picks one.
//  - Settings has a Models tab after Computers with Subscriptions, Tico Gateway, Defaults and
//    Limits; old links (AI providers, Subscriptions, Chat, the Models card on Computers) open it;
//  - Add subscription is one sheet (ui/app/plan-signin.ts): pick a plan and its sign-in starts with only the kind sent;
//    a cloud computer on its way is waited for; Claude takes one code (pasted ones go by themselves), ChatGPT shows
//    one to copy; done closes the sheet; a failure shows the server's sentence; no computer and Paste instead are the
//    ways out; an API key is a key and an optional name; the page never keeps a link, code or value;
//  - Reconnect is the same sheet from its second step, asking first when runs would restart; Edit (name, owner) and
//    Remove from the row's menu;
//  - Models lists subscriptions under each owner (avatar, name, count; the viewer first, "No owner" last), each tool's
//    plans in pick order with the plan in use, the next one and a held one's end marked;
//  - Computers: one row per computer with a picker per AI tool: People (a person's plans), Single plans, then the
//    computer's own login; a person's plans show the plan in use and why under the picker; a switch that stops runs
//    asks in the page first (409 confirm_restart), then sends confirm; an older computer says Needs update; Gateway off
//    reads None;
//  - a computer's pop-up still lists one connection per AI tool, its own login, and the queue line;
//  - the bot editor has Model and Computer and no subscription line; a phone has no sideways page scroll.
// SUBS_SHOTS=<dir> saves desktop and phone screenshots, dark and light.
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile, harnesses} = require('./support/page.cts') as import('./support/page.cts').Api;
const {unrouted} = require('./support/routes.cts') as import('./support/routes.cts').Api;
const SHOTS = process.env.SUBS_SHOTS || '';
type Page = import('playwright').Page;
type Browser = import('playwright').Browser;
type Route = import('playwright').Route;
type Kind = {kind: string; label: string; runtime: string; secret_label: string; help: string; connect?: boolean; owned?: boolean};
type Machine = {id: string; label: string; owner_id: string; last_seen_at: string; version: string; platform: string; bots: string[];
  accepts_member_bots: boolean; readiness: {runtimes: Record<string, {installed: boolean; authenticated: string}>};
  turn_counts: {running: number; starting: number; waiting: number; queued: number; waits: unknown[]}; turn_summary: string;
  release?: string; cloud_machine?: {state: string}; gateway_fallback?: boolean;
  update?: {state: string; label: string; release: string; commit: string; outdated: boolean; server_release: string; error: string}};
type Sub = {id: string; name: string; kind: string; runtime: string; label: string; status: string; status_detail: string; computers: string[];
  usage: {window?: string | null; used_pct?: number | null; cost_usd_month?: number | null; resets_at?: string; reason?: string; weekly_resets_at?: string; weekly_used_pct?: number} | null;
  added_by: string; owner_id?: string; owner_name?: string; order?: number | null; in_use_on?: string[]};
type Assignment = {runner_id: string; runtime: string; subscription_id: string; person?: string; set?: Record<string, unknown> | null};
// A sign-in as the fake computer reports it; the test reads its id and state.
type Login = {[key: string]: unknown; id: string; state: string; url: string};
// The request bodies the fake server reads (what the page sent); other fields stay unknown.
type Body = {[key: string]: unknown; kind?: string; name?: string; secret?: string; owner_id?: string; confirm?: boolean;
  subscription?: string | null; person?: string; code?: string; runner_id?: string; runtime?: string; preset?: string; enabled?: string[];
  model?: string; expected_revision?: number};
type Write = {p: string; method: string; body?: Body; confirm?: string | null};
type Data = {machines: Machine[]; subs: Sub[]; assignments: Assignment[]; capable: string[]; people?: Record<string, string | number>[];
  providers: {[key: string]: unknown; enabled: string[]; default: {runtime: string; model: string}; revision: number; preset: string;
    providers: {id: string; label: string; gateway?: boolean}[]};
  gateway: Record<string, unknown>; models: Record<string, unknown>[]; levels: Record<string, unknown>;
  connect?: string[]; connectRefused?: string; connectWaking?: number; connectBroken?: string; connectState?: string; connectHeld?: Promise<unknown> | null; connectNotYours?: boolean;
  keys?: (string | undefined)[]; login?: Login};
declare var SETTINGS_TAB: string;
declare function settingsShow(tab: string): void;
declare function settingsEditBot(slug?: string, inline?: boolean): void;
declare function subsUsageHTML(sub: {id: string; name: string; kind: string; usage?: {window?: string | null; used_pct?: number | null} | null}): string;

const KINDS: Kind[] = [
  {kind: 'claude_token', label: 'Claude plan', runtime: 'claude', secret_label: 'Token from claude setup-token', help: 'Run claude setup-token on a computer signed in to the plan, then paste the token.', connect: true, owned: true},
  {kind: 'codex_login', label: 'ChatGPT plan', runtime: 'codex', secret_label: "Codex's auth.json", help: 'Paste ~/.codex/auth.json from a computer where codex login is signed in.', connect: true, owned: true},
  {kind: 'anthropic_key', label: 'Anthropic API key', runtime: 'claude', secret_label: 'API key', help: ''},
  {kind: 'openai_key', label: 'OpenAI API key', runtime: 'codex', secret_label: 'API key', help: ''}];

const PLAN_NAMES: Record<string, string> = {claude_token: 'Claude', codex_login: 'ChatGPT', anthropic_key: 'Anthropic API', openai_key: 'OpenAI API'};

function fixtures(): Data {
  const now = new Date().toISOString();
  const ready = {installed: true, authenticated: 'ready'}, missing = {installed: true, authenticated: 'missing'};
  const machine = (id: string, label: string, operator: string, bots: string[] = [], runtimes: Machine['readiness']['runtimes'] = {codex: ready, claude: ready}, extra: Partial<Machine> = {}): Machine => ({id, label, owner_id: operator, last_seen_at: now,
    version: '0.5.14', platform: 'linux', bots, accepts_member_bots: true, readiness: {runtimes},
    turn_counts: {running: 0, starting: 0, waiting: 0, queued: 0, waits: []}, turn_summary: '0 running · 0 starting · 0 waiting', ...extra});
  return {
    machines: [
      machine('r1', 'Tico Team box', 'ana', ['builder', 'writer', 'ops'], {claude: ready, codex: ready, musecode: ready},
        {version: '0.5.4', release: '0.5.19',
          turn_counts: {running: 1, starting: 0, waiting: 0, queued: 55, waits: []}, turn_summary: '1 running · 0 starting · 55 queued · 0 waiting'}),
      machine('r2', 'Cloud · shared', 'ana', ['scout'], {claude: missing, codex: missing}),
      machine('r3', 'Cloud · Video Owner', 'ana', ['video'], {claude: missing, codex: missing},
        {cloud_machine: {state: 'stopped'}, last_seen_at: new Date(Date.now() - 2 * 3600e3).toISOString()}),
      machine('r4', "Sam's Mac", 'sam', [], {claude: ready, codex: missing}, {version: '0.4.2',
        turn_counts: {running: 0, starting: 0, waiting: 3, queued: 0, waits: []},
        update: {state: 'needs_update', label: 'Needs update', release: '0.4.2', commit: 'c0ffee'.padEnd(40, '0'), outdated: true, server_release: '0.5.14', error: ''}}),
    ],
    subs: [
      {id: 's1', name: 'Claude Max (Ana)', kind: 'claude_token', runtime: 'claude', label: 'Claude plan', status: 'working', status_detail: '',
        computers: ['r1', 'r2', 'r3'], usage: {window: '5h', used_pct: 62, cost_usd_month: null}, added_by: 'ana', owner_id: 'human:ana', owner_name: 'Ana'},
      {id: 's2', name: 'ChatGPT Pro (Ana)', kind: 'codex_login', runtime: 'codex', label: 'ChatGPT plan', status: 'working', status_detail: '',
        computers: ['r1'], usage: {window: 'week', used_pct: 13, cost_usd_month: null, resets_at: new Date(Date.now() + ((4 * 24 + 15) * 60 + 10) * 60000).toISOString()}, added_by: 'ana'},
      {id: 's3', name: 'OpenAI key (company)', kind: 'openai_key', runtime: 'codex', label: 'OpenAI API key', status: 'working', status_detail: '',
        computers: [], usage: {window: null, used_pct: null, cost_usd_month: 41.2}, added_by: 'ana', owner_id: '', owner_name: ''},
      {id: 's4', name: 'Claude Max (Sam)', kind: 'claude_token', runtime: 'claude', label: 'Claude plan', status: 'needs_reconnect', status_detail: 'Token expired',
        computers: [], usage: null, added_by: 'sam', owner_id: 'human:sam', owner_name: 'Sam'},
      {id: 's5', name: 'ChatGPT Team (Cy)', kind: 'codex_login', runtime: 'codex', label: 'ChatGPT plan', status: 'working', status_detail: '',
        computers: ['r2'], usage: {window: 'week', used_pct: 0, cost_usd_month: null}, added_by: 'cy'},
      {id: 's6', name: 'Claude Max (Dee)', kind: 'claude_token', runtime: 'claude', label: 'Claude plan', status: 'working', status_detail: '',
        computers: ['r3'], usage: {window: null, used_pct: null, cost_usd_month: null, reason: 'Waiting for a computer to report'}, added_by: 'dee'},
    ],
    assignments: [{runner_id: 'r1', runtime: 'claude', subscription_id: 's1'}, {runner_id: 'r1', runtime: 'codex', subscription_id: 's2'},
      {runner_id: 'r2', runtime: 'claude', subscription_id: 's1'}, {runner_id: 'r3', runtime: 'claude', subscription_id: 's1'}],
    capable: ['r1', 'r2', 'r3'],
    providers: {configured: true, enabled: ['anthropic', 'openai', 'tico'], default: {runtime: 'codex', model: 'gpt-6-luna'}, revision: 4,
      auto_fallback: true, check_models: [], check_order: [{runtime: 'grok'}, {runtime: 'codex'}], preset: 'subscriptions-gateway',
      presets: ['subscriptions-gateway', 'subscriptions', 'gateway', 'own-keys'],
      meeting_model: '', providers: [{id: 'anthropic', label: 'Anthropic'}, {id: 'openai', label: 'OpenAI'}, {id: 'tico', label: 'Tico', gateway: true}]},
    gateway: {connected: true, state: 'connected', working: true, automatic: true, editable: true, preview: 'tgw…x9k', url: '', default_url: 'https://gateway.tico.dev', revision: 2, spent_usd: 188, cap_usd: 1000},
    models: [
      {id: 'gpt-6-luna', label: 'GPT 6 Luna', provider: 'openai', runtime: 'codex'},
      {id: 'claude-sonnet-5-5', label: 'Claude Sonnet 5.5', provider: 'anthropic', runtime: 'claude'},
      {id: 'meeting-1', label: 'Meeting 1.0', provider: 'tico', runtime: 'tico', meeting: true}],
    levels: {warning_per_person: 40, stop_per_person: 100, people: 3, warning: 120, stop: 300, in_use: 18},
  };
}

async function open(browser: Browser, {viewport = {width: 1440, height: 900}, theme = 'dark', as = 'owner', tab = 'models', old = false, mutate}: {viewport?: {width: number; height: number}; theme?: string; as?: 'owner' | 'member'; tab?: string; old?: boolean; mutate?: (d: Data) => void} = {}) {
  const data = fixtures();
  mutate?.(data);
  const page = await browser.newPage({viewport, serviceWorkers: 'block'});
  const errors: string[] = [], writes: Write[] = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.addInitScript(([t, saved]) => { try { localStorage.setItem('tico.theme', t); sessionStorage.setItem('tico.settings.tab', saved); } catch {} }, [theme, tab] as [string, string]);
  const me = {owner: {id: 'ana', role: 'owner', name: 'Ana'}, member: {id: 'sam', role: 'member', name: 'Sam'}}[as];
  Object.assign(me, {email: me.id + '@example.com', cloud: true, registered: true});
  const policy = {see: {everyone: true}, read: {everyone: true}, write: {everyone: true}};
  const bots = [
    {name: 'builder', display_name: 'Builder', status: 'active', state: 'active', owner_id: 'ana', repo: 'bot-builder', can_manage: true, revision: 3,
      users: [{id: 'ana', name: 'Ana'}], bot_owners: [], resolved_runtime: 'claude', resolved_model: 'claude-sonnet-5-5', model: 'claude-sonnet-5-5', harness: 'claude',
      machine: {runner_id: 'r1', label: 'Tico Team box', owner_id: 'ana', last_seen_at: new Date().toISOString()},
      my_access: {see: true, read: true, write: true}, access_policy: policy},
  ];
  // A refusal as the server sends it (backend/app.py Problem handler: the extra facts beside code and detail).
  const problem = (code: string, detail: string, extra?: Record<string, unknown>) => ({error: {code, detail, retryable: false, ...extra}});
  await page.route('**/*', async (route: Route) => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname, method = req.method();
    const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (url.origin !== 'https://tico-ui.test') return route.abort();
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1]!)))
      return route.fulfill({contentType: ui[1]!.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]!), 'utf8')});
    if (p.startsWith('/vendor/fonts/') && fs.existsSync(uiFile(p.slice(1)))) return route.fulfill({contentType: 'font/woff2', body: fs.readFileSync(uiFile(p.slice(1)))});
    if (/\.(?:js|ts)$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: ''});
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
      const body = req.postDataJSON() as Body; writes.push({p, method, body});
      Object.assign(data.providers, body.preset ? {preset: body.preset} : {}, {revision: data.providers.revision + 1});
      return json(data.providers);
    }
    if (p === '/api/v2/providers/gateway') return json(data.gateway);
    if (p === '/api/v2/settings/turn-levels' && method === 'GET') return json(data.levels);
    if (old && /^\/api\/v2\/(model|computer)-subscriptions/.test(p)) return json({detail: 'Not Found'}, 404);
    if (p === '/api/v2/model-subscriptions' && method === 'GET') return json({subscriptions: data.subs, kinds: KINDS, connect: data.connect || ['claude_token', 'codex_login']});
    if (p === '/api/v2/model-subscriptions/connect' && method === 'POST') {
      const body = req.postDataJSON() as Body; writes.push({p, method, body});
      const sub = data.subs.find(s => s.id === body.subscription);
      if (sub?.computers.includes('r1') && !body.confirm)
        return json(problem('confirm_restart', 'Reconnecting restarts running tasks', {running: 1, sessions: 2, computers: sub.computers.length}), 409);
      if (data.connectRefused) return json(problem('offline', data.connectRefused), 409);
      if (data.connectBroken) return json(problem('setup', data.connectBroken, {ref: 'sf-1a2b3c4d'}), 500);
      if (data.connectHeld) await data.connectHeld;
      const kind = KINDS.find(k => k.kind === (body.kind || sub!.kind))!;
      // The name the server gives it: the saved one's, or the plan's with the next number.
      const base = PLAN_NAMES[kind.kind]!, taken = data.subs.filter(s => s.kind === kind.kind).length;
      const name = sub?.name || (taken ? `${base} ${taken + 1}` : base);
      // No computer online yet, the cloud one on its way: no sign-in, and the page asks again.
      if (data.connectWaking) { data.connectWaking -= 1; return json({id: '', kind: 'connect', runner_id: '', runtime: kind.runtime, state: 'requested', computer: '', not_yours: false, name, waking: true}); }
      const waiting = !data.connectState;
      data.login = {id: `l${writes.length}`, runner_id: 'r1', runtime: kind.runtime, kind: 'connect', state: data.connectState || 'waiting', url: waiting ? 'https://sign-in.example/authorize?x=1' : '',
        code: waiting && kind.runtime === 'codex' ? 'ABCD-1234' : '', lines: [], message: '', accepts_code: kind.runtime === 'claude', code_sent: false,
        created_at: new Date().toISOString(), updated_at: new Date().toISOString(), computer: 'Tico Team box', not_yours: Boolean(data.connectNotYours), name, waking: false};
      return json(data.login);
    }
    const signIn = p.match(/^\/api\/v2\/computers\/r1\/logins\/([^/]+)(?:\/(code|cancel))?$/);
    if (signIn && data.login?.id === signIn[1]) {
      if (method === 'GET') return json(data.login);
      const body = req.postDataJSON() as Body; writes.push({p, method, body});
      if (signIn[2] === 'code') Object.assign(data.login!, {code_sent: true});
      if (signIn[2] === 'cancel') Object.assign(data.login!, {state: 'canceled', url: ''});
      return json(data.login);
    }
    if (signIn) return json({detail: 'Not Found'}, 404);
    if (p === '/api/v2/model-subscriptions' && method === 'POST') {
      const body = req.postDataJSON() as Body; writes.push({p, method, body});
      const kind = KINDS.find(k => k.kind === body.kind)!;
      const row = {id: `s9${data.subs.length}`, name: body.name || PLAN_NAMES[body.kind!]!, kind: body.kind!, runtime: kind.runtime, label: kind.label, status: 'new', status_detail: '', computers: [], usage: null, added_by: me.id};
      data.subs.push(row);
      return json(row);
    }
    const one = p.match(/^\/api\/v2\/model-subscriptions\/([^/]+)$/);
    if (one) {
      const sub = data.subs.find(s => s.id === one[1])!;
      if (method === 'POST') {
        const body = req.postDataJSON() as Body; writes.push({p, method, body});
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
    if (p === '/api/v2/computer-subscriptions' && method === 'GET') return json({assignments: data.assignments, capable: data.capable, people: data.people || []});
    if (p === '/api/v2/computer-subscriptions' && method === 'PUT') {
      const body = req.postDataJSON() as Body; writes.push({p, method, body});
      // Tico Team box is running a task: a switch there stops it, so the server asks first.
      if (body.runner_id === 'r1' && !body.confirm) return json(problem('confirm_restart', 'Switching restarts running tasks', {running: 2, sessions: 5}), 409);
      data.assignments = data.assignments.filter(a => !(a.runner_id === body.runner_id && a.runtime === body.runtime));
      if (body.subscription) data.assignments.push({runner_id: body.runner_id!, runtime: body.runtime!, subscription_id: body.subscription});
      if (body.person) data.assignments.push({runner_id: body.runner_id!, runtime: body.runtime!, subscription_id: 'p2', person: body.person,
        set: {person: body.person, person_name: 'Ana', plans: 3, on: 'p2', on_name: 'Plan 2', why: {kind: 'soonest', plan: null, until: null}}});
      for (const s of data.subs) s.computers = data.assignments.filter(a => a.subscription_id === s.id).map(a => a.runner_id);
      return json({runner_id: body.runner_id, runtime: body.runtime, subscription_id: body.subscription, stopped: body.confirm ? 2 : 0, sessions_reset: body.confirm ? 5 : 0});
    }
    const fallback = p.match(/^\/api\/v2\/runners\/([^/]+)\/gateway-fallback$/);
    if (fallback && method === 'POST') {
      const body = req.postDataJSON() as Body & {on?: boolean}; writes.push({p, method, body});
      data.machines.find(m => m.id === fallback[1])!.gateway_fallback = Boolean(body.on);
      return json({gateway_fallback: Boolean(body.on)});
    }
    if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
  await page.goto('https://tico-ui.test/#/settings');
  await page.locator(`#settings-${tab}`).waitFor();
  return {page, errors, writes, data};
}
async function shot(page: Page, name: string, target: Page | import('playwright').Locator = page) {
  if (!SHOTS) return;
  fs.mkdirSync(SHOTS, {recursive: true});
  await page.waitForTimeout(150);
  await target.screenshot({path: path.join(SHOTS, name + '.png'), ...(target === page ? {fullPage: true} : {})});
}
// Each group heading on Settings > Models: [name, count, whether it is the viewer's own].
const groupHeads = (where: import('playwright').Locator) => where.locator('.subs-group-head').evaluateAll(list => list.map(h =>
  [h.querySelector('.subs-group-name')!.textContent, h.querySelector('.subs-group-title > .muted')!.textContent, !!h.querySelector('.subs-you')]));
const modelsReady = async (page: Page) => { await page.locator('#set-subs [data-subs-row]').first().waitFor(); await page.locator('#set-prov').waitFor(); };
// Waits for the fake server to have seen a write.
async function until(page: Page, check: () => boolean, what: string) {
  for (let i = 0; i < 100; i++) { if (check()) return; await page.waitForTimeout(30); }
  throw new Error(`timed out waiting for ${what}`);
}

async function navigation(browser: Browser) {
  const {page, errors} = await open(browser, {tab: 'general'});
  const tabs = await page.locator('#settings-tabs > [data-settings-tab]').evaluateAll(list => list.map(b => b.dataset.settingsTab));
  assert.deepEqual(tabs.slice(tabs.indexOf('devices'), tabs.indexOf('devices') + 2), ['devices', 'models'], 'Models sits after Computers');
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
    assert.equal(await page.evaluate(alias => { settingsShow(alias); return location.hash; }, alias), alias === 'credentials' ? '#/tools/keys?show=credentials' : '#/tools');
  }
  assert.deepEqual(errors, []);
  await page.close();
  console.log('Models tab and old links: ok');
}

async function modelsTab(browser: Browser) {
  const {page, errors, writes} = await open(browser);
  await modelsReady(page);
  const pane = page.locator('#settings-models');
  assert.deepEqual(await pane.locator(':scope > section.card > header h2').allInnerTexts(), ['Subscriptions', 'Tico Gateway', 'Defaults', 'Limits']);
  // No intro, no banner, no subtitles on the cards.
  assert.equal(await pane.locator('section.card > header .sub').count(), 0);
  // Rows: name, kind (the tool for a plan), a computer count, usage (share left and the hours to its reset, or the month's
  // cost), Reconnect when the login stopped working. No computer's state in the Usage column (#335).
  // Grouped by owner: the viewer's group first, then the others by name, then No owner; no Owner column.
  assert.deepEqual(await groupHeads(pane), [['Ana', '· 1 plan', true], ['Sam', '· 1 plan', false], ['No owner', '· 4 subscriptions', false]]);
  assert.equal(await pane.locator('.subs-owner').count(), 0);
  const rows = await pane.locator('[data-subs-row]').evaluateAll(list => list.map(r => [
    r.querySelector('.subs-name')!.textContent, r.querySelector('.subs-kind')!.textContent,
    r.querySelector('.subs-count .tnum')!.textContent,
    [...(r.querySelector('.subs-use')!.querySelectorAll('.subs-usage-w, .tnum, .subs-usage:not(:has(.tnum)), .pill') as unknown as Element[])].map(e => e.textContent).join(' ')]));
  assert.deepEqual(rows, [
    ['Claude Max (Ana)', 'Claude', '3', '5h 38% left'],
    ['Claude Max (Sam)', 'Claude', '0', 'Sign in again'],
    ['Claude Max (Dee)', 'Claude', '1', 'No reading yet'],
    ['ChatGPT Pro (Ana)', 'ChatGPT', '1', 'week 87% left resets in 111h'],
    ['ChatGPT Team (Cy)', 'ChatGPT', '1', 'week 100% left'],
    ['OpenAI key (company)', 'OpenAI API key', '0', '$41.20 this month']]);
  assert.equal(await pane.locator('[data-subs-row=s4] [data-subs-reconnect]').innerText(), 'Sign in again');
  assert.match(await pane.locator('[data-subs-row=s4] .subs-use').innerText(), /Sign-in expired/);
  assert.doesNotMatch(await pane.locator('#set-subs').innerText(), /Computer offline|Bring it online|No computer assigned|Connection unavailable|Held until|In use ·/);
  // The hours until the window resets sit after the share left; the tooltip names the window and the reset time.
  assert.match((await pane.locator('[data-subs-row=s2] .subs-usage').getAttribute('title'))!, /^Week: 87% left · resets \S.* \(in 111h\)$/);
  assert.equal(await pane.locator('[data-subs-row=s1] .subs-reset').count(), 0);
  // The bar is the share left: a fresh plan's is full.
  assert.equal(await pane.locator('[data-subs-row=s5] .subs-bar b').getAttribute('style'), 'width:100%');
  // No reading: muted, with the server's reason in the tooltip.
  assert.equal(await pane.locator('[data-subs-row=s6] .subs-bar').count(), 0);
  assert.equal(await pane.locator('[data-subs-row=s6] .subs-none').getAttribute('title'), 'Waiting for a computer to report');
  // An offline computer is an amber dot on the count, naming it; a click opens Settings > Computers.
  assert.equal(await pane.locator('[data-subs-row=s1] [data-subs-offline]').getAttribute('title'), 'Cloud · Video Owner is offline');
  assert.equal(await pane.locator('[data-subs-row=s2] [data-subs-offline]').count(), 0);
  assert.equal(await pane.locator('[data-subs-row=s4] .subs-count').getAttribute('title'), 'Not on a computer. Give it to one in Settings › Computers.');
  assert.equal(await pane.locator('[data-subs-row=s1] .subs-count').getAttribute('title'), 'Tico Team box, Cloud · shared, Cloud · Video Owner');
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
  assert.deepEqual(writes.find(w => w.p === '/api/v2/providers')!.body, {enabled: ['anthropic', 'openai', 'tico'], model: 'gpt-6-luna', expected_revision: 4, preset: 'subscriptions'});
  await page.waitForFunction(() => { const t = document.querySelector<HTMLInputElement>('[data-gateway-toggle]'); return t && !t.disabled && !t.checked; });
  // Defaults: the old controls, compact; a save leaves the preset and providers alone.
  assert.deepEqual(await pane.locator('#set-prov .models-row label').allInnerTexts(), ['Default model', 'Meeting model', 'Fall back when a plan runs out', 'Check model']);
  assert.equal(await pane.locator('#set-prov-model').inputValue(), 'gpt-6-luna');
  const before = writes.length;
  await pane.locator('#set-prov-save').click();
  await until(page, () => writes.length > before, 'the Defaults save');
  const saved = writes.at(-1)!.body!;
  assert.equal('decide_level' in saved, false);
  assert.deepEqual(saved.enabled, ['anthropic', 'openai', 'tico']);
  assert.equal('preset' in saved, false);
  // Limits: Warn at and Stop at, in runs per person.
  assert.deepEqual(await pane.locator('#turn-levels-form .models-row label').allInnerTexts(), ['Warn at', 'Stop at']);
  assert.equal(await pane.locator('#turn-levels-warn').inputValue(), '40');
  assert.deepEqual(errors, []);
  await page.close();
  console.log('Models tab cards: ok');
}

// The sheet that adds or reconnects a subscription (ui/app/plan-signin.ts), and the step it is on.
const sheet = (page: Page) => page.locator('dialog.plan-sheet[open]');
const onStep = (page: Page, step: string) => page.locator(`dialog.plan-sheet[open][data-plan-step=${step}]`).waitFor();
// A code as a provider's page hands it back: long, one word.
const LONG_CODE = 'c0de-' + 'x'.repeat(40) + '#state';
// What the page reads from and writes to the clipboard, held by the test.
async function clipboard(page: Page, text: string | null) {
  await page.evaluate(held => {
    const w = window as unknown as {copied: string[]};
    w.copied = [];
    Object.defineProperty(navigator, 'clipboard', {configurable: true, value: {
      readText: () => held === null ? Promise.reject(new Error('denied')) : Promise.resolve(held),
      writeText: (value: string) => { w.copied.push(value); return Promise.resolve(); }}});
  }, text);
}

// Taps the button that opens the provider, without the test opening that page.
const tapOpen = (dialog: import('playwright').Locator) => dialog.locator('[data-plan-open]').evaluate((el: HTMLElement) => {
  el.addEventListener('click', event => event.preventDefault(), {once: true});
  el.click();
});

async function addSubscription(browser: Browser) {
  const {page, errors, writes, data} = await open(browser);
  await modelsReady(page);
  await page.locator('#subs-add').click();
  const dialog = sheet(page);
  // Pick: the two plans, then the two keys, smaller. Nothing to type, nobody to choose, nothing to save.
  await onStep(page, 'pick');
  assert.deepEqual(await dialog.locator('[data-plan-kind]').evaluateAll(list => list.map(el => (el as HTMLElement).dataset.planKind)),
    ['claude_token', 'codex_login', 'anthropic_key', 'openai_key']);
  assert.deepEqual(await dialog.locator('.plan-pick').evaluateAll(list => list.map(el => (el as HTMLElement).dataset.planKind)), ['claude_token', 'codex_login']);
  assert.equal(await dialog.locator('input, select, textarea, button[type=submit]').count(), 0);
  assert.equal(writes.length, 0);
  // A tap on the plan starts its sign-in at once: the kind is all the page sends.
  await dialog.locator('[data-plan-kind=claude_token]').click();
  await onStep(page, 'signin');
  assert.deepEqual(writes.at(-1), {p: '/api/v2/model-subscriptions/connect', method: 'POST', body: {kind: 'claude_token'}});
  const link = dialog.locator('[data-plan-open]');
  assert.equal(await link.getAttribute('href'), 'https://sign-in.example/authorize?x=1');
  assert.equal(await link.getAttribute('target'), '_blank');
  // One field, for the code the provider's page hands back; no place for a pasted value on this path.
  assert.deepEqual(await dialog.locator('input, textarea, select').evaluateAll(list => list.map(el => [(el as HTMLInputElement).name, el.getAttribute('autocomplete')])), [['code', 'one-time-code']]);
  // Nothing is waited for until the provider's page was opened from here; then the line and its clock show.
  assert.equal(await dialog.locator('[data-plan-wait=tab]').isVisible(), false);
  await tapOpen(dialog);
  assert.equal(await dialog.locator('[data-plan-wait=tab]').isVisible(), true);
  assert.equal(await dialog.locator('[name=code]').count(), 1, 'the tap redraws nothing');
  assert.equal(await dialog.locator('[data-plan-elsewhere]').count(), 0);
  // A typed code waits for Sign in.
  await dialog.locator('[name=code]').fill('typed-code');
  await page.waitForTimeout(100);
  assert.equal(writes.length, 1);
  await dialog.locator('[data-plan-entry] button[type=submit]').click();
  await until(page, () => writes.at(-1)!.p.endsWith('/code'), 'the typed code');
  assert.deepEqual(writes.at(-1)!.body, {code: 'typed-code'});
  await dialog.locator('[data-plan-wait=finish]').waitFor();
  assert.equal(await dialog.locator('[name=code]').count(), 0);
  // Failed: the server's sentence as sent; what else it sends beside it is not shown.
  const rejected = data.login!.id;
  Object.assign(data.login!, {state: 'failed', url: '', message: 'Claude did not take that code.', category: 'not_a_known_word', trace: 'sk-ant-oat01-not-for-the-page', lines: ['sk-ant-oat01-not-for-the-page']});
  await onStep(page, 'failed');
  assert.equal(await dialog.locator('.plan-said').innerText(), 'Claude did not take that code.');
  assert.doesNotMatch(await dialog.innerText(), /sk-ant-oat|not_a_known_word/);
  assert.equal(await dialog.locator('[data-plan-retry]').count(), 1);
  assert.equal(await dialog.locator('[data-plan-paste]').count(), 1);
  // Try again is a fresh sign-in with its field back. Paste reads the clipboard in the tap, and a whole code goes by itself.
  await clipboard(page, `  ${LONG_CODE}\n`);
  await dialog.locator('[data-plan-retry]').click();
  await onStep(page, 'signin');
  assert.notEqual(data.login!.id, rejected);
  await dialog.locator('[data-plan-clip]').click();
  await until(page, () => writes.at(-1)!.p === `/api/v2/computers/r1/logins/${data.login!.id}/code`, 'the pasted code');
  assert.deepEqual(writes.at(-1)!.body, {code: LONG_CODE});
  // Done when the server says so: the check and the name it gave, then the sheet goes by itself and the row is there.
  data.subs.push({id: 's8', name: 'Claude 4', kind: 'claude_token', runtime: 'claude', label: 'Claude plan', status: 'new', status_detail: '', computers: [], usage: null, added_by: 'ana'});
  Object.assign(data.login!, {state: 'done', url: ''});
  await onStep(page, 'done');
  assert.equal(await dialog.locator('.plan-done b').innerText(), 'Claude 4');
  await dialog.waitFor({state: 'detached'});
  await page.locator('[data-subs-row=s8]').waitFor();
  // A browser that will not hand the clipboard over puts the cursor in the field; a paste there goes by itself too.
  await clipboard(page, null);
  await page.locator('#subs-add').click();
  await dialog.locator('[data-plan-kind=claude_token]').click();
  await onStep(page, 'signin');
  await dialog.locator('[data-plan-clip]').click();
  await page.waitForFunction(() => (document.activeElement as HTMLInputElement | null)?.name === 'code');
  await dialog.locator('[name=code]').evaluate((field: HTMLInputElement, code) => { field.value = code; field.dispatchEvent(new Event('paste', {bubbles: true})); }, LONG_CODE);
  await until(page, () => writes.at(-1)!.p.endsWith('/code'), 'the code pasted in the field');
  assert.deepEqual(writes.at(-1)!.body, {code: LONG_CODE});
  await dialog.locator('[data-plan-close]').click();
  await dialog.waitFor({state: 'detached'});
  // ChatGPT: the code to type there, large, with Copy above the button that opens it; nothing to type here.
  await clipboard(page, '');
  await page.locator('#subs-add').click();
  await dialog.locator('[data-plan-kind=codex_login]').click();
  await onStep(page, 'signin');
  assert.equal(await dialog.locator('[data-plan-code]').innerText(), 'ABCD-1234');
  assert.equal(await dialog.locator('input, textarea').count(), 0);
  assert.equal(await dialog.locator('[data-plan-copy], [data-plan-open]').evaluateAll(list => list.map(el => el.hasAttribute('data-plan-copy') ? 'copy' : 'open').join()), 'copy,open');
  await dialog.locator('[data-plan-copy]').click();
  await page.waitForFunction(() => (window as unknown as {copied: string[]}).copied.length === 1);
  assert.deepEqual(await page.evaluate(() => (window as unknown as {copied: string[]}).copied), ['ABCD-1234']);
  data.subs.push({id: 's7', name: 'ChatGPT', kind: 'codex_login', runtime: 'codex', label: 'ChatGPT plan', status: 'new', status_detail: '', computers: [], usage: null, added_by: 'ana'});
  Object.assign(data.login!, {state: 'done', url: '', code: ''});
  await dialog.waitFor({state: 'detached'});
  await page.locator('[data-subs-row=s7]').waitFor();
  // An API key: the key and a name nobody has to give. No owner.
  await page.locator('#subs-add').click();
  await dialog.locator('[data-plan-kind=anthropic_key]').click();
  await onStep(page, 'key');
  assert.deepEqual(await dialog.locator('input, select, textarea').evaluateAll(list => list.map(el => (el as HTMLInputElement).name)), ['secret', 'name']);
  assert.equal(await dialog.locator('[name=secret]').getAttribute('type'), 'password');
  await dialog.locator('button[type=submit]').click();
  await page.waitForTimeout(100);
  assert.equal(writes.at(-1)!.p.endsWith('/model-subscriptions'), false, 'an empty key sends nothing');
  await dialog.locator('[name=secret]').fill('sk-ant-api03-abc');
  await dialog.locator('button[type=submit]').click();
  await dialog.waitFor({state: 'detached'});
  assert.deepEqual(writes.at(-1), {p: '/api/v2/model-subscriptions', method: 'POST', body: {kind: 'anthropic_key', secret: 'sk-ant-api03-abc'}});
  await page.locator('#subs-add').click();
  await dialog.locator('[data-plan-kind=openai_key]').click();
  await dialog.locator('[name=secret]').fill('sk-proj-abc');
  await dialog.locator('[name=name]').fill('OpenAI (ops)');
  await dialog.locator('button[type=submit]').click();
  await dialog.waitFor({state: 'detached'});
  assert.deepEqual(writes.at(-1)!.body, {kind: 'openai_key', secret: 'sk-proj-abc', name: 'OpenAI (ops)'});
  // Nothing of a sign-in is kept in the page's storage.
  assert.doesNotMatch(await page.evaluate(() => JSON.stringify([{...localStorage}, {...sessionStorage}])), /sign-in\.example|ABCD-1234|c0de-|sk-/);
  assert.deepEqual(errors, []);
  await page.close();
  console.log('add subscription: ok');
}

async function connectPlan(browser: Browser) {
  const {page, errors, writes, data} = await open(browser);
  await modelsReady(page);
  const dialog = sheet(page);
  const connects = () => writes.filter(w => w.p.endsWith('/connect')).length;
  // No computer online, a cloud computer on its way: the sheet waits and asks again until the sign-in exists.
  data.connectWaking = 1;
  await page.locator('#subs-add').click();
  await dialog.locator('[data-plan-kind=claude_token]').click();
  await dialog.locator('[data-plan-wait=computer]').waitFor();
  assert.equal(await dialog.getAttribute('data-plan-step'), 'starting');
  assert.match(await dialog.locator('[data-plan-clock]').innerText(), /^\d+:\d\d$/);
  assert.equal(await dialog.locator('input, [data-plan-open], [data-plan-paste]').count(), 0);
  await onStep(page, 'signin');
  assert.equal(connects(), 2);
  assert.deepEqual(writes.filter(w => w.p.endsWith('/connect')).map(w => w.body), [{kind: 'claude_token'}, {kind: 'claude_token'}]);
  // Cancel ends the sign-in on the computer and closes the sheet.
  await dialog.locator('[data-plan-cancel]').click();
  await dialog.waitFor({state: 'detached'});
  assert.ok(writes.at(-1)!.p.endsWith('/cancel'));
  // Cancel while the computer starts: nothing asks again.
  data.connectWaking = 5;
  await page.locator('#subs-add').click();
  await dialog.locator('[data-plan-kind=claude_token]').click();
  await dialog.locator('[data-plan-wait=computer]').waitFor();
  const asked = connects();
  await dialog.locator('[data-plan-cancel]').click();
  await dialog.waitFor({state: 'detached'});
  await page.waitForTimeout(3300);
  assert.equal(connects(), asked);
  data.connectWaking = 0;
  // A computer that has not opened the provider's page yet: still starting, with nothing to tap but Cancel.
  data.connectState = 'starting';
  await page.locator('#subs-add').click();
  await dialog.locator('[data-plan-kind=codex_login]').click();
  await dialog.locator('[data-plan-wait=start]').waitFor();
  data.connectState = '';
  Object.assign(data.login!, {state: 'waiting', url: 'https://sign-in.example/device', code: 'WXYZ-9876'});
  await onStep(page, 'signin');
  assert.equal(await dialog.locator('[data-plan-code]').innerText(), 'WXYZ-9876');
  // A sign-in the server no longer has (it restarted for a new release) has ended: the sheet says so and offers Try again.
  delete data.login;
  await onStep(page, 'failed');
  assert.equal(await dialog.locator('[data-plan-retry]').count(), 1);
  await dialog.locator('[data-plan-retry]').click();
  await onStep(page, 'signin');
  // Out of time is a failure like any other.
  Object.assign(data.login!, {state: 'expired', url: '', code: ''});
  await onStep(page, 'failed');
  await dialog.locator('[data-plan-close]').click();
  await dialog.waitFor({state: 'detached'});
  // A start the server refuses for another reason: its sentence, with its reference after it.
  data.connectBroken = 'Tico could not start the sign-in.';
  await page.locator('#subs-add').click();
  await dialog.locator('[data-plan-kind=claude_token]').click();
  await onStep(page, 'failed');
  assert.match(await dialog.locator('.plan-said').innerText(), /sf-1a2b3c4d$/);
  await dialog.locator('[data-plan-close]').click();
  await dialog.waitFor({state: 'detached'});
  data.connectBroken = '';
  // No computer at all: the two ways out, and no field until one is taken.
  data.connectRefused = 'No computer is online to run the sign-in';
  await page.locator('#subs-add').click();
  await dialog.locator('[data-plan-kind=claude_token]').click();
  await onStep(page, 'none');
  assert.equal(await dialog.locator('[data-plan-computer]').getAttribute('href'), '#/settings');
  assert.equal(await dialog.locator('[data-plan-computer]').getAttribute('data-gs-tab'), 'devices');
  assert.equal(await dialog.locator('input, textarea').count(), 0);
  // Paste instead: one field for the value; an empty one sends nothing and says nothing about any other path.
  await dialog.locator('[data-plan-paste]').click();
  await onStep(page, 'paste');
  assert.deepEqual(await dialog.locator('input, textarea, select').evaluateAll(list => list.map(el => [(el as HTMLInputElement).name, (el as HTMLInputElement).type])), [['secret', 'password']]);
  const before = writes.length;
  await dialog.locator('button[type=submit]').click();
  await page.waitForTimeout(100);
  assert.equal(writes.length, before);
  assert.equal(await dialog.locator('[data-plan-err]').isVisible(), false);
  await dialog.locator('[name=secret]').fill('sk-ant-oat01-abc');
  await dialog.locator('button[type=submit]').click();
  await dialog.waitFor({state: 'detached'});
  assert.deepEqual(writes.at(-1), {p: '/api/v2/model-subscriptions', method: 'POST', body: {kind: 'claude_token', secret: 'sk-ant-oat01-abc'}});
  // ChatGPT's is a file: the picker fills the box.
  await page.locator('#subs-add').click();
  await dialog.locator('[data-plan-kind=codex_login]').click();
  await onStep(page, 'none');
  await dialog.locator('[data-plan-paste]').click();
  await dialog.locator('[data-plan-file-input]').setInputFiles({name: 'auth.json', mimeType: 'application/json', buffer: Buffer.from('{"auth_mode": "chatgpt", "tokens": {"id_token": "x"}}')});
  await page.waitForFunction(() => (document.querySelector('#plan-secret') as HTMLTextAreaElement | null)?.value.includes('chatgpt'));
  await dialog.locator('button[type=submit]').click();
  await dialog.waitFor({state: 'detached'});
  assert.deepEqual(writes.at(-1)!.body, {kind: 'codex_login', secret: '{"auth_mode": "chatgpt", "tokens": {"id_token": "x"}}'});
  data.connectRefused = '';
  // Reconnect is the same sheet from its second step. With a task running, the page asks before the sign-in starts.
  await page.locator('[data-subs-more=s1]').click();
  await page.locator('#subs-menu [data-subs-act=reconnect]').click();
  const confirm = page.locator('dialog.settings-confirm[open]');
  await confirm.waitFor();
  assert.equal(await dialog.getAttribute('data-plan-step'), 'starting');
  await confirm.locator('[data-confirm-ok]').click();
  await onStep(page, 'signin');
  assert.deepEqual(writes.at(-1)!.body, {subscription: 's1', confirm: true});
  // Closing cancels the sign-in on the computer.
  await dialog.locator('[data-plan-close]').click();
  await dialog.waitFor({state: 'detached'});
  assert.ok(writes.at(-1)!.p.endsWith('/cancel'));
  // No to the restart: the sheet goes, and nothing started.
  const starts = connects();
  await page.locator('[data-subs-more=s1]').click();
  await page.locator('#subs-menu [data-subs-act=reconnect]').click();
  await confirm.locator('[data-confirm-cancel]').click();
  await dialog.waitFor({state: 'detached'});
  assert.equal(connects(), starts + 1);
  assert.equal(writes.at(-1)!.body!.confirm, undefined);
  // Someone else's computer: the sheet says which before the person signs in.
  data.connectNotYours = true;
  await page.locator('[data-subs-row=s4] [data-subs-reconnect]').click();
  await onStep(page, 'signin');
  assert.deepEqual(writes.at(-1)!.body, {subscription: 's4'});
  assert.equal(await dialog.locator('[data-plan-elsewhere]').count(), 1);
  await dialog.locator('[data-plan-close]').click();
  await dialog.waitFor({state: 'detached'});
  data.connectNotYours = false;
  // Closed while the start is on its way: the sign-in it started is cancelled, not left running out of sight.
  let release!: (value?: unknown) => void; data.connectHeld = new Promise(done => { release = done; });
  const held = connects();
  await page.locator('#subs-add').click();
  await dialog.locator('[data-plan-kind=claude_token]').click();
  await until(page, () => connects() === held + 1, 'the start');
  await dialog.locator('[data-plan-close]').click();
  await dialog.waitFor({state: 'detached'});
  release(); data.connectHeld = null;
  await until(page, () => writes.at(-1)!.p.endsWith('/cancel'), 'the cancel');
  assert.equal(data.login!.state, 'canceled');
  // The same for Try again after a failure.
  await page.locator('#subs-add').click();
  await dialog.locator('[data-plan-kind=claude_token]').click();
  await onStep(page, 'signin');
  Object.assign(data.login!, {state: 'failed', url: '', message: 'The sign-in command stopped without signing in'});
  await dialog.locator('[data-plan-retry]').waitFor();
  const failedLogin = data.login!.id, again = connects();
  data.connectHeld = new Promise(done => { release = done; });
  await dialog.locator('[data-plan-retry]').click();
  await until(page, () => connects() === again + 1, 'the second start');
  await dialog.locator('[data-plan-close]').click();
  await dialog.waitFor({state: 'detached'});
  release(); data.connectHeld = null;
  await until(page, () => writes.at(-1)!.p.endsWith('/cancel') && data.login!.id !== failedLogin, 'the cancel of the retry');
  assert.equal(data.login!.state, 'canceled');
  assert.deepEqual(errors, []);
  await page.close();
  console.log('connect a plan: ok');
}

async function rowMenu(browser: Browser) {
  const {page, errors, writes} = await open(browser);
  await modelsReady(page);
  // Edit: an API key's name only (no owner); a plan's name and owner (only a change is sent). No own-bots line here.
  await page.locator('[data-subs-more=s3]').click();
  assert.deepEqual(await page.locator('#subs-menu [data-subs-act]').allInnerTexts(), ['Edit', 'Reconnect', 'Remove']);
  await page.locator('#subs-menu [data-subs-act=edit]').click();
  const rename = page.locator('dialog.subs-dialog[open]');
  assert.equal(await rename.locator('h2').innerText(), 'Edit OpenAI key (company)');
  assert.equal(await rename.locator('select').count(), 0);
  assert.equal(await rename.locator('.subs-note').count(), 0);
  await rename.locator('input[name=name]').fill('OpenAI (ops)');
  await rename.locator('button[type=submit]').click();
  await rename.waitFor({state: 'detached'});
  assert.deepEqual(writes.at(-1), {p: '/api/v2/model-subscriptions/s3', method: 'POST', body: {name: 'OpenAI (ops)'}});
  await page.locator('[data-subs-more=s1]').click();
  await page.locator('#subs-menu [data-subs-act=edit]').click();
  const owner = page.locator('dialog.subs-dialog[open]');
  assert.equal(await owner.locator('select[name=owner]').inputValue(), 'ana');
  await owner.locator('select[name=owner]').selectOption('sam');
  await owner.locator('button[type=submit]').click();
  await owner.waitFor({state: 'detached'});
  assert.deepEqual(writes.at(-1), {p: '/api/v2/model-subscriptions/s1', method: 'POST', body: {name: 'Claude Max (Ana)', owner_id: 'human:sam'}});
  // Reconnect by pasting, behind the sign-in the row's pill starts: a new value only, and the sign-in is called off.
  await page.locator('[data-subs-row=s4] [data-subs-reconnect]').click();
  const again = page.locator('dialog.plan-sheet[open]');
  await again.locator('[data-plan-open]').waitFor();
  await again.locator('[data-plan-paste]').click();
  await until(page, () => writes.at(-1)!.p.endsWith('/cancel'), 'the sign-in called off');
  assert.equal(await again.locator('input[name=name]').count(), 0);
  await again.locator('#plan-secret').fill('sk-ant-oat01-new');
  await again.locator('button[type=submit]').click();
  await again.waitFor({state: 'detached'});
  assert.deepEqual(writes.at(-1), {p: '/api/v2/model-subscriptions/s4', method: 'POST', body: {secret: 'sk-ant-oat01-new'}});
  // With a task running on a computer given it, the page asks once, before the sign-in; the value pasted after that
  // carries the yes, with a new Idempotency-Key.
  await page.locator('[data-subs-more=s2]').click();
  await page.locator('#subs-menu [data-subs-act=reconnect]').click();
  const ask = page.locator('dialog.settings-confirm[open]');
  await ask.waitFor();
  assert.equal(await ask.locator('.tmodal-title').innerText(), 'Reconnect ChatGPT Pro (Ana)?');
  assert.equal(await ask.locator('p').innerText(), '1 running task stops and restarts, and their sessions start fresh.');
  assert.deepEqual(await ask.locator('button').allInnerTexts(), ['Cancel', 'Reconnect']);
  await ask.locator('[data-confirm-ok]').click();
  const live = page.locator('dialog.plan-sheet[open]');
  await live.locator('[data-plan-open]').waitFor();
  await live.locator('[data-plan-paste]').click();
  await live.locator('#plan-secret').fill('{"auth_mode": "chatgpt"}');
  await live.locator('button[type=submit]').click();
  await live.waitFor({state: 'detached'});
  assert.equal(await page.locator('dialog.settings-confirm[open]').count(), 0);
  assert.deepEqual(writes.filter(w => w.p === '/api/v2/model-subscriptions/s2').map(w => w.body), [{secret: '{"auth_mode": "chatgpt"}', confirm: true}]);
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

async function computersTable(browser: Browser) {
  const {page, errors, writes} = await open(browser, {tab: 'devices'});
  await page.locator('#set-machines [data-subs-pick]').first().waitFor();
  const table = page.locator('#set-machines table.settings-machines');
  assert.deepEqual((await table.locator('thead th').allInnerTexts()).map(t => t.toLowerCase()), ['computer', 'bots', 'claude code', 'codex', 'muse code', 'now']);
  const row = (id: string) => table.locator(`[data-computer-row="${id}"]`);
  assert.equal(await row('r1').locator('.machine-bot-count').innerText(), '3');
  assert.equal(await row('r1').locator('[data-machine-now]').innerText(), '1 running · 55 queued');
  assert.equal(await row('r2').locator('[data-machine-now]').innerText(), 'idle');
  assert.equal(await row('r3').locator('[data-machine-now]').innerText(), 'asleep');
  assert.equal(await row('r4').locator('[data-machine-now]').innerText(), '3 waiting');
  assert.doesNotMatch(await table.innerText(), /0 starting/, 'the long queue line is on the computer page');
  // The picker offers the subscriptions for that tool; with none, the computer's own login, else the Gateway.
  const claude = row('r2').locator('[data-subs-pick=claude]');
  assert.deepEqual(await claude.locator('option').allInnerTexts(), ['Claude Max (Ana) · Ana', 'Claude Max (Sam) · Sam', 'Claude Max (Dee)', 'Gateway']);
  assert.equal(await claude.inputValue(), 's1');
  assert.deepEqual(await row('r1').locator('[data-subs-pick=codex] option').allInnerTexts(), ['ChatGPT Pro (Ana)', 'OpenAI key (company)', 'ChatGPT Team (Cy)', "This computer's own login"]);
  assert.equal(await row('r4').locator('[data-subs-needs-update]').count(), 2, 'an older computer says Needs update');
  assert.equal(await row('r1').locator('.machine-chip').count(), 1, 'Muse Code shows its login as before');
  // Under its own column a tool's chip says its state, not the tool's name again.
  assert.equal((await row('r1').locator('.machine-chip').innerText()).trim(), 'Signed in');
  // No running tasks: the switch saves at once.
  await row('r2').locator('[data-subs-pick=codex]').selectOption('s3');
  await page.waitForFunction(() => document.querySelector<HTMLElement>('[data-computer-row=r2] [data-subs-pick=codex]')?.dataset.current === 's3');
  assert.deepEqual(writes.at(-1)!.body, {runner_id: 'r2', runtime: 'codex', subscription: 's3', confirm: false});
  // Tico Team box is running a task: an in-page confirm with the counts; Cancel puts the picker back.
  await row('r1').locator('[data-subs-pick=claude]').selectOption('s4');
  const confirm = page.locator('dialog.settings-confirm[open]');
  assert.equal(await confirm.locator('.tmodal-title').innerText(), "Switch Tico Team box's Claude Code to Claude Max (Sam)?");
  assert.equal(await confirm.locator('p').innerText(), '2 running tasks stop and restart, and every Claude Code session on this computer starts fresh.');
  assert.deepEqual(await confirm.locator('button').allInnerTexts(), ['Cancel', 'Switch']);
  await shot(page, 'confirm-dialog-only-dark', confirm);
  await confirm.locator('[data-confirm-cancel]').click();
  await page.waitForFunction(() => document.querySelector<HTMLSelectElement>('[data-computer-row=r1] [data-subs-pick=claude]')?.value === 's1');
  assert.equal(writes.filter(w => w.body?.confirm === true).length, 0);
  // Switch sends confirm and says what restarted.
  await row('r1').locator('[data-subs-pick=claude]').selectOption('s4');
  await page.locator('dialog.settings-confirm[open] [data-confirm-ok]').click();
  await page.waitForFunction(() => document.querySelector<HTMLElement>('[data-computer-row=r1] [data-subs-pick=claude]')?.dataset.current === 's4');
  assert.deepEqual(writes.at(-1)!.body, {runner_id: 'r1', runtime: 'claude', subscription: 's4', confirm: true});
  const switchMessage = /Tico Team box's Claude Code: Claude Max \(Sam\) · 2 tasks restarted/;
  const switchedToast = page.locator('#toast, .toast', {hasText: switchMessage}).last();
  await switchedToast.waitFor();
  assert.match(await switchedToast.innerText(), switchMessage);
  // The saved picker and toast precede subsRefresh's redraw, which adds the reconnect action.
  const reconnect = row('r1').locator('[data-subs-reconnect]');
  await reconnect.waitFor();
  assert.equal(await reconnect.count(), 1);
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

// Ana owns three Claude plans, given to Tico Team box as "Ana's plans": Plan 2 in use, Plan 3 next, Plan 1 held.
function personPlans(data: Data) {
  const soon = (days: number) => new Date(Date.now() + days * 864e5).toISOString();
  const plan = (id: string, name: string, extra: Partial<Sub>): Sub => ({id, name, kind: 'claude_token', runtime: 'claude', label: 'Claude plan', status: 'working', status_detail: '',
    computers: ['r1'], usage: {window: 'week', used_pct: 10, cost_usd_month: null, resets_at: soon(3)}, added_by: 'ana', owner_id: 'human:ana', owner_name: 'Ana', order: null, in_use_on: [], ...extra});
  data.subs.push(
    plan('p1', 'Plan 1', {usage: {window: 'week', used_pct: 100, cost_usd_month: null, resets_at: soon(1)}}),
    plan('p2', 'Plan 2', {usage: {window: 'week', used_pct: 50, cost_usd_month: null, resets_at: soon(2)}, order: 1, in_use_on: ['r1']}),
    plan('p3', 'Plan 3', {order: 2}));
  data.people = [{id: 'human:ana', name: 'Ana', claude: 4}, {id: 'human:sam', name: 'Sam', claude: 1}];
  data.assignments = data.assignments.filter(a => !(a.runner_id === 'r1' && a.runtime === 'claude'));
  data.assignments.push({runner_id: 'r1', runtime: 'claude', subscription_id: 'p2', person: 'human:ana',
    set: {person: 'human:ana', person_name: 'Ana', plans: 4, on: 'p2', on_name: 'Plan 2', why: {kind: 'held', plan: 'Plan 1', until: soon(1)}}});
}

async function personGroups(browser: Browser) {
  const {page, errors, writes} = await open(browser, {mutate: personPlans});
  await modelsReady(page);
  const ana = page.locator('#set-subs [data-subs-group="human:ana"]');
  assert.deepEqual(await groupHeads(ana), [['Ana', '· 4 plans', true]]);
  assert.equal(await ana.locator('.subs-group-head .av').count(), 1, "the owner's avatar heads the group");
  // In pick order: the plan in use (tinted, an In use pill naming its computer), the next one, then the held one, which
  // reads 0% left like any plan at its limit.
  const rows = await ana.locator('[data-subs-row]').evaluateAll(list => list.map(r => [
    r.querySelector('.subs-order')!.textContent, r.querySelector('.subs-name')!.firstChild!.textContent!.trim(), r.querySelector('.subs-mark')?.textContent || '']));
  assert.deepEqual(rows.slice(0, 2), [['1', 'Plan 2', 'In use'], ['2', 'Plan 3', 'Next']]);
  assert.deepEqual(rows.slice(2).map(r => r[1]), ['Claude Max (Ana)', 'Plan 1']);
  assert.equal(rows[3]![2], '');
  assert.equal(await ana.locator('[data-subs-row=p2] .subs-mark').getAttribute('title'), 'In use on Tico Team box');
  assert.match(await ana.locator('[data-subs-row=p1] .subs-use').innerText(), /^week\s+0% left\s+resets in 24h$/);
  assert.equal(await ana.locator('[data-subs-row=p1] .subs-bar').getAttribute('class'), 'subs-bar bad');
  assert.equal(await ana.locator('[data-subs-row=p2]').getAttribute('class'), 'subs-in-use');
  await shot(page, 'models-groups');
  await page.close();
  // Computers: the cell names the person's plans, the plan in use and why.
  const computers = await open(browser, {tab: 'devices', mutate: personPlans});
  const cpage = computers.page;
  await cpage.locator('#set-machines [data-subs-pick]').first().waitFor();
  const r1 = cpage.locator('[data-computer-row=r1] [data-subs-pick=claude]');
  assert.equal(await r1.inputValue(), 'person:human:ana');
  assert.deepEqual(await r1.locator('optgroup').evaluateAll(list => list.map(g => g.getAttribute('label'))), ['People', 'Single plans']);
  assert.deepEqual(await r1.locator('optgroup[label=People] option').allInnerTexts(), ["Ana's plans · 4", "Sam's plans · 1"]);
  assert.match(await cpage.locator('[data-computer-row=r1] [data-subs-set-line]').innerText(), /^On Plan 2 · Plan 1 at its limit until \S/);
  // Nobody owns a Codex plan here: no person is offered for Codex.
  assert.equal(await cpage.locator('[data-computer-row=r1] [data-subs-pick=codex] optgroup[label=People]').count(), 0);
  // Picking a person sends `person`, not a subscription.
  await cpage.locator('[data-computer-row=r2] [data-subs-pick=claude]').selectOption('person:human:ana');
  await cpage.locator('[data-computer-row=r2] [data-subs-set-line]').waitFor();
  assert.deepEqual(computers.writes.at(-1)!.body, {runner_id: 'r2', runtime: 'claude', subscription: null, person: 'human:ana', confirm: false});
  assert.equal(await cpage.locator('[data-computer-row=r2] [data-subs-set-line]').innerText(), 'On Plan 2 · soonest reset with usage left');
  await shot(cpage, 'computers-person-plans');
  assert.deepEqual([...errors, ...computers.errors], []);
  assert.deepEqual(writes, []);
  await cpage.close();
  console.log("a person's plans, grouped and picked: ok");
}

// Every one of Ana's plans is at its limit on Cloud · shared, which has no Claude login of its own: its bots wait unless
// the computer's own Fall back to Tico Gateway is on (off until an admin turns it on), and the cell says which.
async function plansRunOut(browser: Browser) {
  const {page, errors, writes} = await open(browser, {tab: 'devices', mutate: data => {
    personPlans(data);
    data.assignments = data.assignments.filter(a => !(a.runner_id === 'r2' && a.runtime === 'claude'));
    data.assignments.push({runner_id: 'r2', runtime: 'claude', subscription_id: 'p2', person: 'human:ana',
      set: {person: 'human:ana', person_name: 'Ana', plans: 3, on: null, on_name: null, why: {kind: 'all_held', plan: null, until: new Date(Date.now() + 864e5).toISOString()}}});
  }});
  const line = page.locator('[data-computer-row=r2] [data-subs-set-line]');
  await line.waitFor();
  // The company's Gateway switch is on (fixtures), yet the computer's own switch decides.
  assert.match(await line.innerText(), /^All at their limit · next reset \S.* · waiting$/);
  await page.locator('[data-computer-row=r2] .machine-name').click();
  const detail = page.locator('#set-machines dialog.computer-detail[open]');
  const box = detail.locator('[data-gateway-fallback=r2]');
  assert.equal(await box.isChecked(), false);
  await box.check();
  await page.waitForFunction(() => document.querySelector('[data-computer-row=r2] [data-subs-set-line]')?.textContent?.endsWith('on the Gateway'));
  assert.deepEqual(writes.at(-1), {p: '/api/v2/runners/r2/gateway-fallback', method: 'POST', body: {on: true}});
  assert.equal(await detail.locator('[data-gateway-fallback=r2]').isChecked(), true);
  assert.deepEqual(errors, []);
  await page.close();
  console.log('plans run out: the computer waits unless its own switch is on: ok');
}

async function gatewayOffAndMember(browser: Browser) {
  const {page, errors} = await open(browser, {tab: 'devices', as: 'member', mutate: data => { data.providers.preset = 'subscriptions'; }});
  await page.locator('#set-machines [data-subs-pick]').first().waitFor();
  const codex = page.locator('[data-computer-row=r2] [data-subs-pick=codex]');
  assert.equal(await codex.locator('option').last().innerText(), 'None', 'Gateway off: no subscription reads None');
  assert.equal(await codex.isDisabled(), true, 'only owners and admins assign');
  // A member reads a computer's Fall back to Tico Gateway and cannot change it.
  await page.locator('[data-computer-row=r2] .machine-name').click();
  const fallback = page.locator('#set-machines dialog.computer-detail[open] [data-gateway-fallback=r2]');
  assert.deepEqual([await fallback.isChecked(), await fallback.isDisabled()], [false, true]);
  await page.keyboard.press('Escape');
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

async function oldServer(browser: Browser) {
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

async function botEditor(browser: Browser) {
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

// Every state of the sheet on a 400 px phone and a desktop, dark and light: it fits without sideways scroll, and on the
// phone what there is to tap sits in the top half of the screen, clear of an open keyboard.
async function sheetScreens(browser: Browser) {
  for (const theme of ['dark', 'light']) {
    for (const [kind, viewport] of [['desktop', {width: 1280, height: 860}], ['phone', {width: 400, height: 800}]] as [string, {width: number; height: number}][]) {
      const {page, errors, data} = await open(browser, {viewport, theme});
      await modelsReady(page);
      const dialog = sheet(page);
      const view = async (name: string) => {
        assert.ok(await page.evaluate(() => { const d = document.querySelector('dialog.plan-sheet[open]')!;
          return Math.max(document.documentElement.scrollWidth - document.documentElement.clientWidth, d.scrollWidth - d.clientWidth); }) <= 0, `${kind} ${name}: no sideways scroll`);
        if (!SHOTS) return;
        fs.mkdirSync(SHOTS, {recursive: true});
        await page.waitForTimeout(300);   // the sheet's fade-in
        await page.screenshot({path: path.join(SHOTS, `sheet-${name}-${kind}-${theme}.png`)});
      };
      const above = async (what: string, name: string) => {
        if (kind !== 'phone') return;
        const box = (await dialog.locator(what).last().boundingBox())!;
        assert.ok(box.y + box.height <= viewport.height / 2, `${name}: ${what} ends at ${Math.round(box.y + box.height)}, in the top half`);
      };
      await page.locator('#subs-add').click();
      await onStep(page, 'pick');
      await view('pick');
      data.connectWaking = 99;
      await dialog.locator('[data-plan-kind=claude_token]').click();
      await dialog.locator('[data-plan-wait=computer]').waitFor();
      await view('starting');
      await dialog.locator('[data-plan-cancel]').click();
      await dialog.waitFor({state: 'detached'});
      data.connectWaking = 0;
      await page.locator('#subs-add').click();
      await dialog.locator('[data-plan-kind=claude_token]').click();
      await onStep(page, 'signin');
      await above('[data-plan-entry] button[type=submit]', 'Claude');
      await view('claude');
      await tapOpen(dialog);
      await view('claude-opened');
      Object.assign(data.login!, {state: 'failed', url: '', message: 'Claude did not take that code.'});
      await onStep(page, 'failed');
      await above('[data-plan-retry]', 'failed');
      await view('failed');
      await dialog.locator('[data-plan-paste]').click();
      await onStep(page, 'paste');
      await above('button[type=submit]', 'paste');
      await view('paste');
      await dialog.locator('[data-plan-close]').click();
      await dialog.waitFor({state: 'detached'});
      await page.locator('#subs-add').click();
      await dialog.locator('[data-plan-kind=codex_login]').click();
      await onStep(page, 'signin');
      await above('[data-plan-open]', 'ChatGPT');
      await view('chatgpt');
      await tapOpen(dialog);
      await view('chatgpt-opened');
      await dialog.locator('[data-plan-cancel]').click();
      await dialog.waitFor({state: 'detached'});
      data.connectRefused = 'No computer is online to run the sign-in';
      await page.locator('#subs-add').click();
      await dialog.locator('[data-plan-kind=codex_login]').click();
      await onStep(page, 'none');
      await view('no-computer');
      await dialog.locator('[data-plan-paste]').click();
      await onStep(page, 'paste');
      await view('paste-chatgpt');
      await dialog.locator('[data-plan-back]').click();
      await onStep(page, 'pick');
      await dialog.locator('[data-plan-close]').click();
      await dialog.waitFor({state: 'detached'});
      data.connectRefused = '';
      await page.locator('#subs-add').click();
      await dialog.locator('[data-plan-kind=anthropic_key]').click();
      await onStep(page, 'key');
      await view('key');
      await dialog.locator('[data-plan-back]').click();
      await dialog.locator('[data-plan-kind=codex_login]').click();
      await onStep(page, 'signin');
      Object.assign(data.login!, {state: 'done', url: '', code: ''});
      await onStep(page, 'done');
      await view('done');
      await dialog.waitFor({state: 'detached'});
      assert.deepEqual(errors, []);
      await page.close();
    }
  }
  console.log('sheet states fit: ok');
}

// A phone (390 px): each plan's reason and its one action sit under the plan's name, inside the screen, with no sideways
// scroll to find them (validation of #3370 found the Usage column off-screen to the right). Desktop keeps its columns.
async function phoneReasons(browser: Browser) {
  // Two more rows: a plan given to no computer, and one whose state the page cannot name (on a computer that is online).
  const extra = (id: string, name: string, status: string, computers: string[]): Sub => ({id, name, kind: 'claude_token', runtime: 'claude',
    label: 'Claude plan', status, status_detail: '', computers, usage: null, added_by: 'ana'});
  const {page, errors} = await open(browser, {viewport: {width: 390, height: 844},
    mutate: d => { d.subs.push(extra('s7', 'Claude Max (Eve)', 'working', []), extra('s8', 'Claude Max (Flo)', 'failing', ['r1'])); }});
  await modelsReady(page);
  const width = 390;
  for (const [id, reason] of [['s1', /^5h\s+38% left$/], ['s2', /^week\s+87% left\s+resets in 111h$/], ['s4', /Sign-in expired\s*Sign in again/],
    ['s6', /^No reading yet$/], ['s7', /^No reading yet$/], ['s8', /^No reading yet$/]] as [string, RegExp][]) {
    const row = page.locator(`[data-subs-row=${id}]`);
    await row.scrollIntoViewIfNeeded();
    const name = (await row.locator('.subs-name').boundingBox())!, use = (await row.locator('.subs-use').boundingBox())!;
    assert.ok(name.x >= 0 && name.x + name.width <= width, `${id}: the plan's name is on screen`);
    assert.ok(use.x >= 0 && use.x + use.width <= width, `${id}: the reason is on screen (x ${Math.round(use.x)}, right ${Math.round(use.x + use.width)})`);
    assert.ok(use.y >= name.y + name.height - 1, `${id}: the reason sits under the plan's name`);
    assert.match(await row.locator('.subs-use').innerText(), reason);
  }
  // The sign-in action is a button a thumb can reach on the screen, and it opens the sign-in sheet.
  const again = page.locator('[data-subs-row=s4] [data-subs-reconnect]');
  const box = (await again.boundingBox())!;
  assert.ok(box.x >= 0 && box.x + box.width <= width, 'Sign in again is on screen');
  await again.click();
  await sheet(page).waitFor();
  assert.deepEqual(errors, []);
  await page.close();
  console.log('phone reasons under the name: ok');
}

// #335: the Usage column shows what is left of each plan and when it resets, nothing about computers. One line, the
// tighter window (the server's reading is the most-used one), the reset in hours; the colour follows what is left.
// A person's next and held plans count the computers their set is on.
async function usageLeft(browser: Browser) {
  const inHours = (h: number) => new Date(Date.now() + h * 3600e3 + 10 * 60e3).toISOString();
  const plan = (id: string, name: string, extra: Partial<Sub>): Sub => ({id, name, kind: 'claude_token', runtime: 'claude', label: 'Claude plan', status: 'working', status_detail: '',
    computers: [], usage: null, added_by: 'ana', owner_id: 'human:ana', owner_name: 'Ana', order: null, in_use_on: [], ...extra});
  const mutate = (d: Data) => {
    d.subs = [
      // AC-1: 38% used in the week, the reset 98 hours away.
      plan('w', 'Weekly', {computers: ['r1'], order: 1, in_use_on: ['r1', 'r2', 'r3'], usage: {window: 'week', used_pct: 38, cost_usd_month: null, resets_at: inHours(98)}}),
      // AC-2 with Chris's note: the 5-hour window is the tighter one, so it is the one line.
      plan('f', 'Five hour', {order: 2, usage: {window: '5h', used_pct: 90, cost_usd_month: null, resets_at: inHours(3), weekly_resets_at: inHours(50), weekly_used_pct: 40}}),
      // AC-3: at its weekly limit.
      plan('h', 'Held', {usage: {window: 'week', used_pct: 100, cost_usd_month: null, resets_at: inHours(30)}}),
      // AC-6: no computer has reported.
      plan('n', 'No reading', {order: 3, usage: {window: null, used_pct: null, cost_usd_month: null, reason: 'Waiting for a computer to report'}}),
      {id: 'g', name: 'Chatgpt', kind: 'codex_login', runtime: 'codex', label: 'ChatGPT plan', status: 'working', status_detail: '', computers: ['r1'],
        usage: {window: 'week', used_pct: 85, cost_usd_month: null, resets_at: inHours(60)}, added_by: 'ana'},
      {id: 'k', name: 'Company key', kind: 'anthropic_key', runtime: 'claude', label: 'Anthropic API key', status: 'working', status_detail: '', computers: [],
        usage: {window: null, used_pct: null, cost_usd_month: 12.4}, added_by: 'ana', owner_id: '', owner_name: ''},
    ];
    // AC-4/AC-5: Ana's plans are on four computers, Cloud · Video Owner offline; an older server lists only the plan in use's.
    d.machines.push({...d.machines[1]!, id: 'r5', label: 'Builder (cloud)'});
    d.assignments = ['r1', 'r2', 'r3', 'r5'].map(runner_id => ({runner_id, runtime: 'claude', subscription_id: 'w', person: 'human:ana'}));
  };
  for (const viewport of [{width: 1280, height: 860}, {width: 390, height: 844}]) {
    const {page, errors} = await open(browser, {viewport, mutate});
    await modelsReady(page);
    const pane = page.locator('#set-subs');
    const use = (id: string) => pane.locator(`[data-subs-row=${id}] .subs-use`);
    // AC-1: a green bar 62% full, "62% left · resets in 98h", nothing else.
    assert.match(await use('w').innerText(), /^week\s+62% left\s+resets in 98h$/);
    assert.equal(await use('w').locator('.subs-bar').getAttribute('class'), 'subs-bar');
    assert.equal(await use('w').locator('.subs-bar b').getAttribute('style'), 'width:62%');
    assert.match((await use('w').locator('.subs-usage').getAttribute('title'))!, /^Week: 62% left · resets \S.* \(in 98h\)$/);
    // AC-2: the tighter window alone, its reset in hours; amber when 6-20% is left, red at 5% or less.
    assert.match(await use('f').innerText(), /^5h\s+10% left\s+resets in 3h$/);
    assert.equal(await use('f').locator('.subs-bar').getAttribute('class'), 'subs-bar warn');
    assert.match(await use('g').innerText(), /^week\s+15% left\s+resets in 60h$/);
    // AC-3: at its limit, 0% left and an empty red bar; "Held until" nowhere.
    assert.match(await use('h').innerText(), /^week\s+0% left\s+resets in 30h$/);
    assert.equal(await use('h').locator('.subs-bar').getAttribute('class'), 'subs-bar bad');
    assert.equal(await use('h').locator('.subs-bar b').getAttribute('style'), 'width:0%');
    const edge = (id: string) => use(id).locator('.subs-bar').evaluate(e => getComputedStyle(e).boxShadow);
    assert.notEqual(await edge('h'), await edge('w'), 'the empty bar keeps a red edge');
    // The colour's edges: green above 20% left, amber from 20% to 6%, red at 5% or less.
    assert.deepEqual(await page.evaluate(() => [79, 80, 94, 95].map(used => {
      const span = document.createElement('span');
      span.innerHTML = subsUsageHTML({id: 'x', name: 'x', kind: 'claude_token', usage: {window: 'week', used_pct: used}});
      return span.querySelector('.subs-bar')!.className;
    })), ['subs-bar', 'subs-bar warn', 'subs-bar warn', 'subs-bar bad']);
    // AC-6: no reading, muted, the server's reason in the tooltip.
    assert.equal(await use('n').innerText(), 'No reading yet');
    assert.equal(await use('n').locator('.subs-none').getAttribute('title'), 'Waiting for a computer to report');
    // AC-4/AC-5: every one of Ana's plans counts the set's four computers, with the amber dot naming the offline one.
    for (const id of ['w', 'f', 'h', 'n']) {
      assert.equal(await pane.locator(`[data-subs-row=${id}] .subs-count .tnum`).innerText(), '4', `${id} counts its set's computers`);
      assert.equal(await pane.locator(`[data-subs-row=${id}] [data-subs-offline]`).getAttribute('title'), 'Cloud · Video Owner is offline');
    }
    assert.equal(await pane.locator('[data-subs-row=w] .subs-count').getAttribute('title'), 'Tico Team box, Cloud · shared, Cloud · Video Owner, Builder (cloud)');
    assert.equal(await pane.locator('[data-subs-row=w] .subs-mark').innerText(), 'In use');
    assert.equal(await pane.locator('[data-subs-row=f] .subs-mark').innerText(), 'Next');
    assert.doesNotMatch(await pane.innerText(), /Computer offline|Bring it online|No computer assigned|Connection unavailable|Held until|In use ·/);
    // AC-7: the tool, not "plan"; API keys keep their kind.
    const kinds = await pane.locator('[data-subs-row] .subs-kind').allInnerTexts();
    assert.deepEqual(kinds, ['Claude', 'Claude', 'Claude', 'Claude', 'Anthropic API key', 'ChatGPT']);
    assert.match(await pane.locator('[data-subs-row=k] .subs-use').innerText(), /^\$12\.40 this month$/);
    // AC-9: on a phone each percent and reset is on screen, and nothing scrolls sideways.
    if (viewport.width === 390) {
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth) <= 0, 'no sideways scroll');
      const dot = (await pane.locator('[data-subs-row=w] [data-subs-offline]').boundingBox())!;
      assert.ok(dot.width >= 40 && dot.height >= 40 && dot.x + dot.width <= 390, `the offline dot is a thumb-sized target (${dot.width}x${dot.height})`);
      for (const id of ['w', 'f', 'h', 'g']) {
        const box = (await use(id).locator('.subs-reset').boundingBox())!;
        assert.ok(box.x >= 0 && box.x + box.width <= 390, `${id}: the reset is on screen`);
      }
    }
    await shot(page, `models-usage-left-${viewport.width}`, pane);
    // The dot opens Settings > Computers.
    await pane.locator('[data-subs-row=w] [data-subs-offline]').click();
    await page.locator('#set-machines').waitFor();
    assert.deepEqual(errors, []);
    await page.close();
  }
  console.log('usage left and next reset: ok');
}

async function screens(browser: Browser) {
  for (const theme of ['dark', 'light']) {
    for (const [kind, viewport] of [['desktop', {width: 1280, height: 860}], ['phone', {width: 390, height: 844}]] as [string, {width: number; height: number}][]) {
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
  const browser = await chromium.launch({headless: true, channel: (process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined) as string});
  try {
    await navigation(browser); await modelsTab(browser); await addSubscription(browser); await connectPlan(browser); await rowMenu(browser);
    await computersTable(browser); await personGroups(browser); await plansRunOut(browser); await gatewayOffAndMember(browser); await oldServer(browser); await botEditor(browser); await sheetScreens(browser); await phoneReasons(browser); await usageLeft(browser); await screens(browser);
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
