// Webhooks (ui/app/settings-webhooks.ts) in their three homes against a fake server: incoming on Automations (a built-in
// row, #/automations/webhooks), outgoing on Tools (#/tools/webhooks), through the Gateway as a fold of Models › Tico
// Gateway. Guards: #/triggers, the old Settings > Webhooks addresses and a saved Triggers or Webhooks tab land on each
// part's home, and Settings has no Webhooks tab; an open item is named in the address; a new incoming webhook (name, preset, keep days) is made in its three calls and its address and
// signing secret show once, gone after Back; a webhook's detail links to its Automations; its call log opens a payload (redacted headers, body) and runs one
// again, while a refused call is recovered instead; Tico Team's built-in webhook is read-only but for Edit; an outgoing endpoint is made with the catalog's
// event picker (a whole group is `group.*`) and shows its secret once; its sends retry, and failed or missed sends
// since a day go again; its edit sets filters and custom headers, and a secret header's value never comes back; the
// picker leaves out events only automations see (outbound: false); the Gateway fold asks for the Gateway first, then makes an
// address shown once; Wake this server shows only on a hosted server. The wake address (owner, self-run server only) is
// set in place, its signing secret stays until Done, a refusal shows under the input, and Turn off clears it. No
// sideways scroll on a phone.
// TICO_SHOTS=<dir> saves screenshots (dark).
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const {html, uiFile} = require('./support/page.cts') as import('./support/page.cts').Api;
const {unrouted} = require('./support/routes.cts') as import('./support/routes.cts').Api;
type Page = import('playwright').Page;
type Relay = {address_prefix: string; created_at: string; wake: boolean | null};
type Trigger = {id: string; name: string; kind: string; event: string; url: string; builtin: boolean; created_by: string; created_at: string;
  last_received: string | null; received_24h: number; refused_24h: number; signed: boolean; preset: string; type_from: string; hourly: number;
  keep_days: number; unheard: string; relay: Relay | null};
type Delivery = {delivery: string; sent_id: string; received_at: string; bytes: number; tasks: number; outcome: string; reason: string;
  type: string; via: string; rules: number; stored: boolean};
type Hook = {id: string; name: string; url: string; host: string; events: string[]; enabled: boolean; paused_reason: string; rate_per_hour: number;
  allow_http: boolean; created_by: string; revision: number; can_change: boolean; old_secret_until: string | null;
  last_7_days: {delivered: number; failed: number; pending: number}; filter: {departments: string[]; bots: string[]; pipelines: string[]};
  headers: {name: string; secret: boolean; set?: boolean; value?: string}[]};
type Send = {id: string; webhook_id: string; type: string; subject: string; state: string; attempts: number; last_status: number | null;
  last_error: string; created_at: string};
type RelayStatus = {key: string; relays: number; cursor: number; last_pull: string | null; last_error: string; pending: number;
  oldest: string | null; deleting: number; active: boolean};
// The request bodies the test reads; a field is optional where some calls omit it.
type Body = {name?: string; url?: string; events?: string[]; preset?: string; keep_days?: number; unheard?: string; since_hours?: number;
  since?: string; after?: string; on?: boolean; wake?: boolean; filter?: {departments: string[]; bots: string[]; pipelines: string[]};
  headers?: {name: string; value?: string; secret: boolean}[]; expected_revision?: number};
type CatalogEvent = {name: string; group: string; outbound?: boolean};
type State = {triggers: Trigger[]; deliveries: Record<string, Delivery[]>; hooks: Hook[]; sends: Record<string, Send[]>; relay: RelayStatus;
  hosted: boolean; role: string; wake: {url: string | null; set_at: string | null; set_by: string | null};
  wakeFail: {status: number; code: string; detail: string} | null; puts: {path: string; body: unknown}[]; posts: {path: string; body: Body}[]};

// Browser-side global the page defines (ui/app).
declare var whMount: (name: string) => unknown;

const shots = process.env.TICO_SHOTS || '';
const CATALOG = JSON.parse(execFileSync(process.env.TICO_PYTHON || 'python3', ['-c', 'import json; from backend.event_catalog import listing; print(json.dumps(listing()))'],
  {cwd: path.join(__dirname, '../..')}).toString()) as CatalogEvent[];
const BASE = 'https://tico.acme.example/hooks';
const AGO = (m: number) => new Date(Date.now() - m * 60000).toISOString();

function trigger(id: string, name: string, more: Partial<Trigger> = {}): Trigger {
  return {id, name, kind: 'webhook', event: 'webhook.' + id, url: `${BASE}/${id}`, builtin: false, created_by: 'human:ana', created_at: AGO(900),
    last_received: null, received_24h: 0, refused_24h: 0, signed: false, preset: 'none', type_from: '', hourly: 60, keep_days: 7, unheard: 'log',
    relay: null, ...more};
}
function server(): State {
  return {
    triggers: [
      trigger('shop', 'Shop', {last_received: AGO(4), received_24h: 12, refused_24h: 2, signed: true, preset: 'github', type_from: 'X-GitHub-Event'}),
      trigger('_tico', 'Tico Team', {builtin: true, url: '', created_by: 'keeper', type_from: '/kind', keep_days: 30}),
    ],
    deliveries: {shop: [
      {delivery: 'd2', sent_id: 'gh-2', received_at: AGO(4), bytes: 2300, tasks: 1, outcome: 'accepted', reason: '', type: 'push', via: 'direct', rules: 1, stored: true},
      {delivery: 'd1', sent_id: '', received_at: AGO(70), bytes: 120, tasks: 0, outcome: 'refused', reason: 'bad signature', type: '', via: 'gateway', rules: 0, stored: true}],
    _tico: [], orders: []},
    hooks: [{id: 'wh1', name: 'Zapier', url: 'https://hooks.zapier.com/a/b', host: 'hooks.zapier.com', events: ['task.*', 'bot.run_failed'], enabled: false,
      paused_reason: 'Failing for 3 days', rate_per_hour: 600, allow_http: false, created_by: 'human:ana', revision: 2, can_change: true, old_secret_until: null,
      last_7_days: {delivered: 40, failed: 3, pending: 0}, filter: {departments: ['engineering'], bots: ['bot:eng'], pipelines: []},
      headers: [{name: 'x-api-key', secret: true, set: true}, {name: 'x-team', secret: false, value: 'acme'}]}],
    sends: {wh1: [{id: 's2', webhook_id: 'wh1', type: 'task.done', subject: 't-9', state: 'failed', attempts: 5, last_status: 500, last_error: 'HTTP 500', created_at: AGO(30)},
      {id: 's1', webhook_id: 'wh1', type: 'bot.run_failed', subject: 'a-1', state: 'delivered', attempts: 1, last_status: 200, last_error: '', created_at: AGO(90)}]},
    relay: {key: 'none', relays: 0, cursor: 0, last_pull: null, last_error: '', pending: 0, oldest: null, deleting: 0, active: false},
    hosted: false,
    role: 'owner',
    wake: {url: null, set_at: null, set_by: null},
    wakeFail: null,            // {status, code, detail}: the next PUT /v2/hooks/wake-url is refused with it
    puts: [],
    posts: [],
  };
}

async function open(browser: import('playwright').Browser, hash: string, viewport: {width: number; height: number} = {width: 1440, height: 900},
  state: State = server(), store: {tab?: string} = {}) {
  const page = await browser.newPage({viewport, serviceWorkers: 'block'});
  const errors: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('dialog', d => d.accept());
  await page.addInitScript(() => { try { localStorage.setItem('tico.theme', 'dark'); } catch {} });
  if (store.tab) await page.addInitScript(tab => sessionStorage.setItem('tico.settings.tab', tab), store.tab);
  await page.route('**/*', route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname;
    const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1]!)))
      return route.fulfill({contentType: ui[1]!.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]!), 'utf8')});
    if (p.startsWith('/vendor/') && fs.existsSync(uiFile(p.slice(1)))) return route.fulfill({body: fs.readFileSync(uiFile(p.slice(1))),
      contentType: p.endsWith('.woff2') ? 'font/woff2' : 'application/javascript'});
    if (/\.(?:js|ts)$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: ''});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const config = {version: '0.4.9', app_name: 'Tico'};
    const post = req.method() === 'POST' ? ((req.postDataJSON() || {}) as Body) : null;
    if (post) state.posts.push({path: p, body: post});
    if (p === '/api/me') return json({id: 'ana', role: state.role, name: 'Ana', email: 'ana@acme.example', cloud: true, registered: true, config});
    if (p === '/api/v2/config') return json(config);
    if (p === '/api/employees') return json([{name: 'eng', display_name: 'Engineer', status: 'active', can_manage: true},
      {name: 'ops', display_name: 'Ops', status: 'active', can_manage: true}]);
    if (p === '/api/issues') return json([]);
    if (p === '/api/humans' || p === '/api/people') return json({people: [{id: 'ana', name: 'Ana'}], teams: {}});
    if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: [], schedules: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/needs-you') return json({items: []});
    if (p === '/api/v2/operations') return json({machines: [], services: []});
    if (p === '/api/v2/event-types') return json({items: CATALOG});
    if (p === '/api/v2/sleep') return json({sleep: {hosted: state.hosted, hours: 0, runners: false}});
    if (p === '/api/v2/system-routines') return json({now: new Date().toISOString(), jobs: [], departments: ['Engineering', 'Sales'], task_rule_departments: [{id: 'engineering', name: 'Engineering'}, {id: 'sales', name: 'Sales'}]});
    if (p === '/api/v2/pipelines') return json({pipelines: [{id: 'general', name: 'General', retired_at: null, stages: []}, {id: 'engineering', name: 'Engineering', retired_at: null, stages: []}]});
    if (p === '/api/v2/triggers' && !post) return json({triggers: state.triggers});
    if (p === '/api/v2/triggers') {
      const id = post!.name!.toLowerCase();
      state.triggers.push(trigger(id, post!.name!));
      return json({trigger: {id, event: 'webhook.' + id, url: `${BASE}/${id}/sek-ret-123`, replayed: false}});
    }
    let m = p.match(/^\/api\/v2\/triggers\/([^/]+)(?:\/(.*))?$/);
    if (m) {
      const t = state.triggers.find(x => x.id === m![1])!, rest = m[2] || '';
      if (rest === '' && post) { Object.assign(t, post); return json(t); }
      if (rest === 'signing') { Object.assign(t, {signed: true, preset: post!.preset!, type_from: t.type_from || ({github: 'X-GitHub-Event', standard: '/type', stripe: '/type'} as Record<string, string>)[post!.preset!] || ''}); return json({trigger: {id: t.id, signed: true, preset: post!.preset, signing_secret: 'whsec_c2VjcmV0', replayed: false}}); }
      if (rest === 'deliveries') return json({deliveries: state.deliveries[t.id] || []});
      if (rest === 'recover') return json({recover: {trigger: t.id, since_hours: post!.since_hours, found: 1, recovered: 1, duplicate: 0, still_refused: 0, more: false}});
      if (rest === 'relay/wake') { t.relay!.wake = post!.wake!; return json({relay: {trigger: t.id, relay_id: 'rl_1', wake: post!.wake}}); }
      if (rest === 'relay') { t.relay = {address_prefix: 'https://gw.tico.dev/v1/hooks/rl_1', created_at: AGO(0), wake: null}; return json({relay: {trigger: t.id, relay_id: 'rl_1', address_prefix: t.relay.address_prefix, address: t.relay.address_prefix + '/gw-secret', replayed: false}}); }
      const d = rest.match(/^deliveries\/([^/]+)(\/replay)?$/);
      if (d && d[2]) return json({replay: {delivery: d[1]! + ':replay:1', of: d[1]!, tasks: 1, rules: 1}});
      if (d) return json({delivery: {id: d[1]!, received_at: AGO(4), type: 'push', via: 'direct', outcome: 'accepted', tasks: 1, rules: 1, reason: '', bytes: 2300,
        expires_at: AGO(-6000), expired: false, content_type: 'application/json',
        headers: {'content-type': 'application/json', 'x-github-event': 'push', 'x-hub-signature-256': '[hidden]'}, body: '{"id":7,"ref":"refs/heads/main"}'}});
    }
    if (p === '/api/v2/webhooks' && !post) return json({items: state.hooks, more: false});
    if (p === '/api/v2/webhooks') {
      const h = {...state.hooks[0]!, id: 'wh2', name: post!.name!, url: post!.url!, host: new URL(post!.url!).host, events: post!.events!, enabled: true, paused_reason: '', revision: 1,
        last_7_days: {delivered: 0, failed: 0, pending: 0}};
      state.hooks.push(h); state.sends.wh2 = [];
      return json({webhook: {...h, secret: 'whsec_b3V0Z29pbmc=', replayed: false}});
    }
    m = p.match(/^\/api\/v2\/webhooks\/([^/]+)(?:\/(.*))?$/);
    if (m) {
      const h = state.hooks.find(x => x.id === m![1])!, rest = m[2] || '';
      if (rest === '') return json({webhook: h, recent_attempts: []});
      if (rest === 'sends') return json({items: state.sends[h.id] || [], more: false});
      if (rest === 'resume') { h.enabled = true; return json({webhook: h}); }
      if (rest === 'replay') return json(post!.after ? {queued: 2, more: false} : {queued: 0, more: true, next_since: '2026-10-20T03:00:00.000000Z', next_after: 'we_1'});
      return json({ok: true});
    }
    if (p === '/api/v2/relay') return json({relay: state.relay});
    // Models, for the Gateway card that holds the Webhooks fold.
    if (p === '/api/v2/models') return json({models: [], harnesses: [], default: {runtime: 'claude', model: ''}, enabled_providers: ['anthropic']});
    if (p === '/api/v2/providers') return json({enabled: ['anthropic'], default: {runtime: 'claude', model: ''}, revision: 1, auto_fallback: true,
      providers: [{id: 'anthropic', label: 'Anthropic'}]});
    if (p === '/api/v2/automations') return json({automations: [], next: null});
    if (p === '/api/v2/hooks/wake-url') {
      if (state.role !== 'owner') return json({error: {code: 'forbidden', detail: 'Only the owner may see or change the address that wakes this server'}}, 403);
      if (req.method() !== 'PUT') return json(state.wake);
      const body = req.postDataJSON() as {url: string | null};
      state.puts.push({path: p, body});
      if (state.wakeFail) { const {status, ...error} = state.wakeFail; state.wakeFail = null; return json({error}, status); }
      state.wake = body.url ? {url: body.url, set_at: AGO(0), set_by: 'human:ana'} : {url: null, set_at: null, set_by: null};
      return json({url: body.url, secret: body.url ? 'whsec_d2FrZS1zZWNyZXQ=' : null});
    }
    if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
  await page.goto('https://tico-ui.test/' + hash);
  return {page, errors, state};
}
// Every animation that ends has ended (a skeleton's shimmer runs forever).
const settled = (page: Page) => page.evaluate(() => Promise.all(document.getAnimations()
  .filter(a => a.effect?.getComputedTiming().iterations !== Infinity).map(a => a.finished.catch(() => {}))));
const noSideways = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1);
const hash = (page: Page) => new URL(page.url()).hash;
const go = (page: Page, to: string) => page.evaluate(to => { location.hash = to; }, to);
// The Gateway's Webhooks fold, drawn again (what reopening it does).
const redraw = (page: Page) => page.evaluate(() => whMount('gateway'));
// The open whDialog as a person sees it: its fields' labels and the space between them, what is ticked or in view, and
// any text not at the body's one size or any line but the footer's (inputs and buttons keep their own borders).
const dialogLook = (page: Page) => page.locator('.wh-dialog').evaluate(d => {
  const form = d.querySelector('form')!, foot = form.querySelector<HTMLElement>('.wh-foot')!, shown = (e: Element) => (e as HTMLElement).offsetParent !== null;
  const fields = [...form.querySelectorAll<HTMLElement>(':scope > label, .wh-sum')].filter(shown);
  const label = (f: HTMLElement) => f.matches('.wh-sum') ? f.querySelector<HTMLElement>('.wh-sum-k')! : f;
  const rect = (e: Element) => e.getBoundingClientRect(), box = rect(d), submit = foot.querySelector('[type=submit]');
  const body = [...form.querySelectorAll<HTMLElement>('*')].filter(e => shown(e) && !e.closest('header'));
  const texts = body.filter(e => [...e.childNodes].some(n => n.nodeType === 3 && n.textContent!.trim()));
  const lined = body.filter(e => !e.matches('input, select, textarea, button') && ['Top', 'Right', 'Bottom', 'Left'].some(side => parseFloat(getComputedStyle(e).getPropertyValue(`border-${side.toLowerCase()}-width`)) > 0));
  return {
    fields: fields.map(f => label(f).firstChild!.textContent!.trim()),
    labelStyle: [...new Set(fields.map(f => { const cs = getComputedStyle(label(f)); return `${cs.color} ${cs.fontSize} ${cs.fontWeight}`; }))],
    gaps: fields.slice(1).map((f, i) => Math.round(rect(f).top - rect(fields[i]!).bottom)),
    rows: [...form.querySelectorAll<HTMLElement>('.wh-sum-row')].map(r => [...r.children].filter(shown).map(c => (c as HTMLElement).innerText.trim())),
    ticks: [...form.querySelectorAll('input[type=checkbox]')].filter(shown).length,
    scrolls: d.scrollHeight > d.clientHeight + 1,
    submitInView: !!submit && rect(submit).bottom <= Math.min(box.bottom, innerHeight) + 0.5 && rect(submit).top >= box.top,
    footPinned: Math.abs(rect(foot).bottom - box.bottom) <= 1.5 && rect(foot).bottom <= innerHeight + 0.5,
    footLast: form.lastElementChild === foot,
    head: [...form.querySelector('header')!.children].map(c => c.tagName.toLowerCase()),
    footButtons: [...foot.querySelectorAll<HTMLElement>('button')].filter(shown).sort((a, b) => rect(a).left - rect(b).left).map(b => b.innerText.trim()),
    sizes: [...new Set(texts.map(e => getComputedStyle(e).fontSize))],
    lines: lined.map(e => e.className || e.tagName),
    boxes: form.querySelectorAll('fieldset, legend').length,
  };
});
const INCOMING = '#wh-incoming', OUTGOING = '#wh-outgoing', GATEWAY = '#wh-gateway', GATEWAY_LINK = '#/settings/models/wh-gateway';

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined} as import('playwright').LaunchOptions);
  const shot = async (page: Page, name: string, el: string) => { if (shots) await page.locator(el).screenshot({path: path.join(shots, name + '.png')}); };
  const view = async (page: Page, name: string) => { if (shots) { await settled(page); await page.screenshot({path: path.join(shots, name + '.png')}); } };
  try {
    {
      // #/triggers lands on Automations' Incoming webhooks; the list has the built-in Tico Team row.
      const {page, errors, state} = await open(browser, '#/triggers');
      let pane = page.locator(INCOMING);
      await pane.locator('[data-wh-open="shop"]').waitFor();
      assert.equal(hash(page), '#/automations/webhooks');
      assert.equal(await page.locator('#au-root').getAttribute('data-screen'), 'detail');
      assert.equal(await page.locator('.au-hd .au-name').innerText(), 'Incoming webhooks');
      assert.match(await pane.locator('[data-wh-open="shop"]').innerText(), /12 in 24h · 2 refused/);
      assert.match(await pane.locator('[data-wh-open="_tico"]').innerText(), /Built-in/);
      await shot(page, 'incoming-list-desktop', INCOMING);
      await view(page, 'automations-incoming-webhooks-desktop-dark');

      // New webhook: create, then signing, then the settings that differ; the secrets show once.
      await pane.locator('[data-wh-new]').click();
      const dialog = page.locator('.wh-dialog');
      await dialog.locator('[name=name]').fill('Orders');
      await dialog.locator('[name=preset]').selectOption('standard');
      assert.equal(await dialog.locator('[name=type_from]').inputValue(), '/type', 'the preset fills where the type comes from');
      await dialog.locator('[name=keep_days]').fill('14');
      await dialog.locator('[name=unheard]').selectOption('note');
      if (shots) { await settled(page); await dialog.screenshot({path: path.join(shots, 'incoming-new-desktop.png')}); }
      await dialog.locator('button[type=submit]').click();
      await dialog.waitFor({state: 'detached'});
      await pane.locator('[data-wh-secret="url"]').waitFor();
      assert.deepEqual(state.posts.map(x => x.path), ['/api/v2/triggers', '/api/v2/triggers/orders/signing', '/api/v2/triggers/orders']);
      assert.deepEqual(state.posts[1]!.body, {preset: 'standard'});
      assert.deepEqual(state.posts[2]!.body, {keep_days: 14, unheard: 'note'});
      assert.equal(await pane.locator('[data-wh-secret="url"] code').innerText(), `${BASE}/orders/sek-ret-123`);
      assert.equal(await pane.locator('[data-wh-secret="signing"] code').innerText(), 'whsec_c2VjcmV0');
      await shot(page, 'incoming-created-desktop', INCOMING);
      await pane.locator('[data-wh-back]').click();
      await pane.locator('[data-wh-open="orders"]').click();
      await pane.locator('.wh-facts').waitFor();
      assert.equal(await pane.locator('[data-wh-secret]').count(), 0, 'shown once');
      assert.match(await pane.locator('.wh-facts').innerText(), /\/hooks\/orders\/…/);
      assert.match(await pane.locator('.wh-facts').innerText(), /Type from\s+\/type/);
      await pane.locator('[data-wh-back]').click();

      // Shop: its Automations link, the call log, View payload and Run again.
      await pane.locator('[data-wh-open="shop"]').click();
      await pane.locator('[data-wh-calls]').waitFor();
      assert.equal(hash(page), '#/automations/webhooks/shop', 'the open webhook is in the address');
      assert.equal(await pane.locator('[data-wh-automations]').getAttribute('href'), '#/automations?q=Shop', 'listeners are Automations');
      assert.equal(await pane.locator('[data-wh-call]').count(), 2);
      assert.equal(await pane.locator('[data-wh-call="d1"] [data-wh-payload]').count(), 1, 'a refused call is kept to look at');
      assert.equal(await pane.locator('[data-wh-call="d1"] [data-wh-replay]').count(), 0, 'a refused call is recovered, not run again');
      assert.match(await pane.locator('[data-wh-call="d1"]').innerText(), /Refused bad signature/);
      await shot(page, 'incoming-detail-desktop', INCOMING);
      await pane.locator('[data-wh-call="d2"] [data-wh-payload]').click();
      const payload = page.locator('.wh-dialog');
      await payload.locator('[data-wh-body]').waitFor();
      assert.match(await payload.locator('[data-wh-headers]').innerText(), /x-hub-signature-256: \[hidden\]/);
      assert.equal(await payload.locator('[data-wh-body]').innerText(), '{\n  "id": 7,\n  "ref": "refs/heads/main"\n}');
      if (shots) { await settled(page); await payload.screenshot({path: path.join(shots, 'payload-desktop.png')}); }
      await payload.locator('[data-close]').click();
      const replayed = page.waitForResponse(r => r.url().endsWith('/deliveries/d2/replay'));
      await pane.locator('[data-wh-call="d2"] [data-wh-replay]').click();
      await replayed;
      assert.ok(state.posts.some(x => x.path === '/api/v2/triggers/shop/deliveries/d2/replay'));
      // Recover checks the kept refused calls again, as far back as they are kept.
      const recovered = page.waitForResponse(r => r.url().endsWith('/triggers/shop/recover'));
      await pane.locator('[data-wh-recover]').click();
      await recovered;
      assert.deepEqual(state.posts.at(-1)!.body, {since_hours: 168});
      await pane.locator('[data-wh-back]').click();

      // Tico Team's built-in: Edit only, keep days only.
      await pane.locator('[data-wh-open="_tico"]').click();
      await pane.locator('.wh-facts').waitFor();
      assert.equal(await pane.locator('[data-wh-edit]').count(), 1);
      assert.equal(await pane.locator('[data-wh-rotate], [data-wh-sign], [data-wh-delete]').count(), 0);
      assert.doesNotMatch(await pane.locator('.wh-facts').innerText(), /Address/);
      await pane.locator('[data-wh-edit]').click();
      assert.deepEqual(await page.locator('.wh-dialog form').evaluate(f => [...(f as HTMLFormElement & {elements: Iterable<HTMLInputElement>}).elements].filter(e => e.name).map(e => e.name)), ['keep_days']);
      await page.locator('.wh-dialog [name=keep_days]').fill('10');
      await page.locator('.wh-dialog button[type=submit]').click();
      await page.locator('.wh-dialog').waitFor({state: 'detached'});
      assert.deepEqual(state.posts.at(-1), {path: '/api/v2/triggers/_tico', body: {keep_days: 10}});
      await pane.locator('[data-wh-back]').click();
      await pane.locator('[data-wh-open="shop"]').waitFor();
      assert.equal(hash(page), '#/automations/webhooks');

      // Outgoing, on Tools. The list says each endpoint's events in words.
      await go(page, '#/tools/webhooks');
      pane = page.locator(OUTGOING);
      await pane.locator('[data-wh-hook="wh1"]').waitFor();
      assert.match(await pane.locator('[data-wh-hook="wh1"]').innerText(), /Paused[\s\S]*40 sent · 3 failed/);
      assert.match(await pane.locator('[data-wh-hook="wh1"]').innerText(), /hooks\.zapier\.com · All tasks, Bot run failed/);
      await shot(page, 'outgoing-list-desktop', OUTGOING);
      // New endpoint at 1440×900: five fields in one rhythm, each section one line at its default, no box, no tick, no
      // scroll, one size of text, Create in view and the footer's line the only one.
      await pane.locator('[data-wh-hook-new]').click();
      const make = page.locator('.wh-dialog');
      await make.locator('[data-wh-events]').waitFor();
      let look = await dialogLook(page);
      assert.deepEqual(look.fields, ['Name', 'URL', 'Events', 'Sends for', 'Headers']);
      assert.equal(look.labelStyle.length, 1, 'every field is labelled alike: ' + look.labelStyle.join(' | '));
      assert.deepEqual(look.gaps, [16, 16, 16, 16]);
      assert.deepEqual(look.rows, [['All events', 'Choose'], ['Every department, bot and pipeline', 'Narrow'], ['None', 'Add header']]);
      assert.equal(look.ticks, 0, 'no checkbox until something is chosen');
      assert.equal(look.scrolls, false, 'nothing to scroll');
      assert.equal(look.submitInView, true, 'Create is in view');
      assert.deepEqual(look.sizes, ['13px'], 'one type size in the body');
      assert.deepEqual(look.lines, ['wh-foot'], 'the footer has the only line');
      assert.equal(look.boxes, 0, 'no fieldset or legend');
      assert.deepEqual(look.head, ['h2'], 'the header holds the title only');
      assert.equal(look.footLast, true);
      assert.deepEqual(look.footButtons, ['Cancel', 'Create']);
      const name = make.locator('[name=name]');
      for (const [k, v] of [['autocomplete', 'off'], ['data-1p-ignore', ''], ['data-lpignore', 'true'], ['data-bwignore', ''], ['data-form-type', 'other']])
        assert.equal(await name.getAttribute(k!), v, `Name ${k} keeps password managers off`);
      if (shots) { await settled(page); await make.screenshot({path: path.join(shots, 'outgoing-new-desktop.png')}); }
      await name.fill('Make.com');
      await make.locator('[name=url]').fill('https://hook.make.com/abc');
      // Choose: areas, plain words, nothing ticked, focus on the first box; the code and description are its tooltip.
      for (const e of CATALOG.filter(x => x.outbound === false)) assert.equal(await make.locator(`[data-ev-name="${e.name}"]`).count(), 0, e.name + ' is for automations only');
      await make.locator('[data-ev-choose]').click();
      const evPick = make.locator('[data-ev-pick]');
      assert.equal(await evPick.getAttribute('role'), 'group');
      assert.equal(await evPick.getAttribute('aria-label'), 'Events');
      assert.equal(await page.evaluate(() => (document.activeElement as HTMLElement).dataset.evName), 'task.created', 'focus is on the first box');
      assert.equal(await evPick.locator('input:checked').count(), 0, 'nothing ticked');
      assert.deepEqual(await evPick.locator('.wh-area-k').allInnerTexts(), ['Tasks', 'Messages', 'Bots', 'Rules', 'Other']);
      const created1 = evPick.locator('label', {has: page.locator('[data-ev-name="task.created"]')});
      assert.equal((await created1.innerText()).trim(), 'Task created');
      assert.equal(await created1.getAttribute('title'), 'task.created · A task was filed.');
      assert.equal((await evPick.locator('label', {has: page.locator('[data-ev-name="strategy.published"]')}).innerText()).trim(), 'Strategy published');
      assert.deepEqual(await evPick.locator('.wh-area-items label').evaluateAll(ls => ls.filter(l => l.getBoundingClientRect().height > 21).map(l => (l as HTMLElement).innerText)), [], 'no label wraps');
      assert.deepEqual(await make.locator('.wh-sum-row').first().locator('button:visible').allInnerTexts(), ['All events']);
      assert.equal((await make.locator('[data-ev-words]').innerText()).trim(), 'None chosen');
      // Nothing ticked: Create says so in the footer, in view.
      await make.locator('button[type=submit]').click();
      const refusal = make.locator('.wh-foot [data-wh-error]');
      await page.waitForFunction(() => document.querySelector('.wh-foot [data-wh-error]')?.textContent === 'Pick at least one event');
      assert.ok(await refusal.evaluate(e => { const r = e.getBoundingClientRect(); return r.height > 0 && r.bottom <= innerHeight && r.top >= 0; }), 'the refusal is in view');
      // The Tasks area, Bot held and Health finding: a whole group goes as group.*, the rest by name.
      await evPick.locator('[data-ev-area="tasks"] [data-ev-head]').check();
      await evPick.locator('[data-ev-name="bot.held"]').check();
      await evPick.locator('[data-ev-name="health.finding"]').check();
      assert.equal(await evPick.locator('[data-ev-area="tasks"] [data-ev-name]:checked').count(), CATALOG.filter(e => e.group === 'task').length);
      assert.equal(await evPick.locator('[data-ev-area="bots"] [data-ev-head]').evaluate(h => (h as HTMLInputElement).indeterminate), true, 'a part-ticked area shows the dash');
      assert.equal((await make.locator('[data-ev-words]').innerText()).trim(), 'All tasks, Bot held, Health finding');
      if (shots) { await settled(page); await make.screenshot({path: path.join(shots, 'outgoing-choose-desktop.png')}); }
      await make.locator('button[type=submit]').click();
      await make.waitFor({state: 'detached'});
      await pane.locator('[data-wh-secret="secret"]').waitFor();
      const created = state.posts.find(x => x.path === '/api/v2/webhooks')!;
      assert.deepEqual(created.body.events, ['task.*', 'bot.held', 'health.finding']);
      assert.equal((await pane.locator('[data-wh-event-facts]').innerText()).trim(), 'All tasks, Bot held, Health finding');
      assert.equal(await pane.locator('[data-wh-event-facts]').getAttribute('title'), 'task.*, bot.held, health.finding', 'the codes are the tooltip');
      assert.equal(await pane.locator('[data-wh-secret="secret"] code').innerText(), 'whsec_b3V0Z29pbmc=');
      await pane.locator('[data-wh-back]').click();
      await pane.locator('[data-wh-hook="wh1"]').click();
      await pane.locator('[data-wh-sends]').waitFor();
      assert.equal(hash(page), '#/tools/webhooks/wh1', 'the open endpoint is in the address');
      assert.equal(await pane.locator('[data-wh-send="s2"] [data-wh-retry]').count(), 1);
      assert.equal(await pane.locator('[data-wh-send="s1"] [data-wh-retry]').count(), 0, 'no retry on a delivered send');
      assert.match(await pane.locator('[data-wh-filter-facts]').innerText(), /Engineering · Engineer/);
      assert.match(await pane.locator('.wh-facts').innerText(), /Sends for\s+Engineering · Engineer/);
      assert.match(await pane.locator('[data-wh-header-facts]').innerText(), /x-api-key secret, x-team/);
      await shot(page, 'outgoing-detail-desktop', OUTGOING);
      // Edit: filters and custom headers. The secret header shows "set", empty, and empty keeps it.
      await pane.locator('[data-wh-hook-edit]').click();
      const edit = page.locator('.wh-dialog');
      await edit.locator('[data-wh-filters]').waitFor();
      const secretRow = edit.locator('[data-hdr]').nth(0);
      assert.equal(await secretRow.locator('[data-hdr-value]').inputValue(), '', 'a secret value is not shown');
      assert.equal(await secretRow.locator('[data-hdr-value]').getAttribute('placeholder'), 'set');
      assert.equal(await secretRow.locator('[data-hdr-value]').getAttribute('type'), 'password');
      assert.equal(await edit.locator('[data-hdr]').nth(1).locator('[data-hdr-value]').inputValue(), 'acme');
      assert.equal(await edit.locator('[data-hdr]').nth(1).locator('[data-hdr-value]').getAttribute('type'), 'text');
      // A section that holds a choice opens; the lists say no "All".
      assert.equal(await edit.locator('[data-ev-pick]').isVisible(), true, 'events not All: open');
      assert.equal(await edit.locator('[data-filter-pick]').isVisible(), true, 'a filter: open');
      assert.equal((await edit.locator('[data-filter-words]').innerText()).trim(), '1 department, 1 bot');
      assert.doesNotMatch(await edit.locator('[data-filter-pick]').innerText(), /\bAll\b/);
      assert.equal((await edit.locator('[data-hdr-none]').innerText()).trim(), '', 'None goes once a header is there');
      await edit.locator('[data-filter="departments"] input[value="engineering"]').uncheck();
      await edit.locator('[data-filter="bots"] input[value="ops"]').check();
      await edit.locator('[data-hdr-add]').click();
      await edit.locator('[data-hdr]').nth(2).locator('[data-hdr-name]').fill('Authorization');
      await edit.locator('[data-hdr]').nth(2).locator('[data-hdr-value]').fill('Bearer abc');
      await edit.locator('[data-hdr]').nth(2).locator('[data-hdr-secret]').check();
      assert.equal(await edit.locator('[data-hdr]').nth(2).locator('[data-hdr-value]').getAttribute('type'), 'password', 'a typed secret is hidden');
      await edit.locator('[data-hdr]').nth(2).locator('[data-hdr-secret]').uncheck();
      assert.equal(await edit.locator('[data-hdr]').nth(2).locator('[data-hdr-value]').getAttribute('type'), 'text');
      await edit.locator('[data-hdr]').nth(2).locator('[data-hdr-secret]').check();
      if (shots) { await settled(page); await edit.screenshot({path: path.join(shots, 'outgoing-edit-desktop.png')}); }
      for (let i = 3; i < 10; i++) await edit.locator('[data-hdr-add]').click();
      assert.equal(await edit.locator('[data-hdr-add]').isVisible(), false, 'ten at most');
      await edit.locator('[data-hdr]').nth(9).locator('[data-hdr-remove]').click();
      assert.equal(await edit.locator('[data-hdr-add]').isVisible(), true);
      await edit.locator('button[type=submit]').click();
      await edit.waitFor({state: 'detached'});
      const saved = state.posts.find(x => x.path === '/api/v2/webhooks/wh1/update')!.body;
      assert.deepEqual(saved.filter, {departments: [], bots: ['eng', 'ops'], pipelines: []});
      assert.deepEqual(saved.headers, [{name: 'x-api-key', secret: true}, {name: 'x-team', value: 'acme', secret: false},
        {name: 'Authorization', value: 'Bearer abc', secret: true}], 'blank rows are dropped and the kept secret is sent without a value');
      assert.equal(saved.expected_revision, 2);
      assert.deepEqual(saved.events, ['task.*', 'bot.run_failed'], 'the events it had');
      await pane.locator('[data-wh-sends]').waitFor();
      const retried = page.waitForResponse(r => r.url().endsWith('/sends/s2/retry'));
      await pane.locator('[data-wh-send="s2"] [data-wh-retry]').click();
      await retried;
      const example = page.waitForResponse(r => r.url().endsWith('/wh1/example'));
      await pane.locator('[data-wh-example]').click();
      await example;
      await pane.locator('[data-wh-hook-pause]').click();
      await page.waitForFunction(() => document.querySelector('[data-wh-hook-pause]')?.textContent === 'Pause');
      // Once it runs again: retry what gave up, or send what it missed, since a day.
      await shot(page, 'outgoing-resumed-desktop', OUTGOING);
      const caught = page.waitForResponse(r => r.url().endsWith('/wh1/recover'));
      await pane.locator('[data-wh-catchup-do="recover"]').click();
      await caught;
      assert.match(state.posts.at(-1)!.body.since!, /^\d{4}-\d\d-\d\d$/);
      // Send missed carries on from where a round that said "more" stopped.
      const missed = page.waitForResponse(r => r.url().endsWith('/wh1/replay') && (r.request().postDataJSON() as Body).after === 'we_1');
      await pane.locator('[data-wh-catchup-do="replay"]').click();
      await missed;
      assert.deepEqual(state.posts.at(-1)!.body, {since: '2026-10-20T03:00:00.000000Z', after: 'we_1'});
      const paths = state.posts.map(x => x.path);
      assert.ok(paths.includes('/api/v2/webhooks/wh1/sends/s2/retry') && paths.includes('/api/v2/webhooks/wh1/example') && paths.includes('/api/v2/webhooks/wh1/resume'), paths.join(' '));

      // Through Tico Gateway, a fold of Models › Tico Gateway that the old address opens: no key yet.
      await go(page, '#/settings/webhooks/gateway');
      pane = page.locator(GATEWAY);
      await pane.locator('[data-wh-no-key]').waitFor();
      assert.equal(hash(page), '#/settings/models');
      assert.equal(await page.locator('[data-gateway-webhooks]').evaluate(d => (d as HTMLDetailsElement).open), true);
      assert.match(await pane.locator('[data-wh-no-key]').innerText(), /Connect the Gateway first/);
      await shot(page, 'gateway-no-key-desktop', '#settings-gateway-card');
      // With a key: make an address, shown once.
      state.relay = {...state.relay, key: 'connected', last_pull: AGO(1), pending: 3, active: true};
      await redraw(page);
      await pane.locator('[data-wh-relay="shop"] [data-wh-relay-do="make"]').click();
      await pane.locator('[data-wh-secret="relay"]').waitFor();
      assert.equal(await pane.locator('[data-wh-secret="relay"] code').innerText(), 'https://gw.tico.dev/v1/hooks/rl_1/gw-secret');
      assert.match(await pane.locator('[data-wh-relay-status]').innerText(), /3 waiting/);
      assert.equal(await pane.locator('[data-wh-relay="_tico"]').count(), 0, 'the built-in has no address to make');
      assert.equal(await pane.locator('[data-wh-wake]').count(), 0, 'no wake switch on a server Tico does not host');
      await shot(page, 'gateway-desktop', '#settings-gateway-card');
      // Hosted: Wake this server, per address.
      state.hosted = true;
      await redraw(page);
      const wake = pane.locator('[data-wh-relay="shop"] [data-wh-wake]');
      await wake.waitFor();
      assert.equal(await wake.isChecked(), true, 'wake: null on a hosted install is on');
      const woke = page.waitForResponse(r => r.url().endsWith('/triggers/shop/relay/wake'));
      await wake.uncheck();
      await woke;
      assert.deepEqual(state.posts.at(-1), {path: '/api/v2/triggers/shop/relay/wake', body: {wake: false}});
      await page.waitForFunction(() => !document.querySelector<HTMLInputElement>('[data-wh-wake]')!.disabled);
      assert.equal(await wake.isChecked(), false);
      const woke2 = page.waitForResponse(r => r.url().endsWith('/triggers/shop/relay/wake'));
      await wake.check();
      await woke2;
      assert.deepEqual(state.posts.at(-1), {path: '/api/v2/triggers/shop/relay/wake', body: {wake: true}});
      await page.waitForFunction(() => !document.querySelector<HTMLInputElement>('[data-wh-wake]')!.disabled);
      await shot(page, 'gateway-wake-desktop', '#settings-gateway-card');
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // Automations lists Incoming webhooks as a built-in row that opens them; Settings has no Webhooks tab.
      const {page, errors} = await open(browser, '#/automations');
      const row = page.locator('tr.au-row[data-row="webhooks"]');
      await row.waitFor();
      assert.match(await row.innerText(), /Incoming webhooks[\s\S]*2 webhooks/);
      await row.locator('a').click();
      await page.locator(`${INCOMING} [data-wh-open="shop"]`).waitFor();
      assert.equal(hash(page), '#/automations/webhooks');
      await go(page, '#/settings');
      await page.locator('#settings-tabs').waitFor();
      assert.equal(await page.locator('[data-settings-tab="webhooks"], [data-settings-tab="triggers"]').count(), 0);
      // Old addresses, each at its part's new home.
      for (const [from, to] of [['#/webhooks', '#/automations/webhooks'], ['#/settings/triggers', '#/automations/webhooks'],
        ['#/settings/webhooks', '#/automations/webhooks'], ['#/settings/webhooks/incoming', '#/automations/webhooks'],
        ['#/settings/webhooks/outgoing', '#/tools/webhooks'], ['#/settings/webhooks/gateway', '#/settings/models']] as [string, string][]) {
        await go(page, '#/tools');
        await page.waitForFunction(() => location.hash === '#/tools');
        await go(page, from);
        await page.waitForFunction(to => location.hash === to, to);
        await page.locator(({'#/automations/webhooks': INCOMING, '#/tools/webhooks': OUTGOING, '#/settings/models': GATEWAY} as Record<string, string>)[to]!).locator(':scope > *').first().waitFor();
      }
      assert.deepEqual(errors, []);
      await page.close();
    }
    for (const tab of ['triggers', 'webhooks']) {
      // The old Triggers or Webhooks tab saved in this browser opens incoming webhooks on Automations.
      const {page, errors} = await open(browser, '#/settings', undefined, undefined, {tab});
      await page.locator(`${INCOMING} [data-wh-open="shop"]`).waitFor();
      assert.equal(hash(page), '#/automations/webhooks');
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // The wake address: set in place, its signing secret shown until Done, a refusal under the input, Turn off.
      const state = server();
      state.relay = {...state.relay, key: 'connected', last_pull: AGO(1), pending: 2, active: true};
      const {page, errors} = await open(browser, GATEWAY_LINK, undefined, state);
      const pane = page.locator(GATEWAY);
      const row = pane.locator('[data-wh-wake-url]');
      await row.locator('[data-wh-wake-value]').waitFor();
      assert.equal(await row.locator('[data-wh-wake-value]').innerText(), 'Off');
      assert.equal(await row.locator('[data-wh-wake-do="off"]').count(), 0, 'nothing to turn off');
      assert.equal(await row.locator('a').getAttribute('href'), 'https://github.com/ticoteam/tico/blob/main/docs/install.md#wake-on-webhook');
      await shot(page, 'wake-off-desktop', GATEWAY);
      // Set: the secret shows once, with its note, and stays across a redraw until Done.
      await row.locator('[data-wh-wake-do="edit"]').click();
      await row.locator('[data-wh-wake-input]').fill('https://wake.acme.example/start');
      await row.locator('[type="submit"]').click();
      const secret = row.locator('[data-wh-secret="wake"]');
      await secret.waitFor();
      assert.deepEqual(state.puts.at(-1), {path: '/api/v2/hooks/wake-url', body: {url: 'https://wake.acme.example/start'}});
      assert.equal(await secret.locator('code').innerText(), 'whsec_d2FrZS1zZWNyZXQ=');
      assert.match(await secret.innerText(), /Signs the tico\.wake posts\. Not shown again\./);
      assert.equal(await row.locator('[data-wh-wake-value]').innerText(), 'https://wake.acme.example/start');
      await shot(page, 'wake-set-desktop', GATEWAY);
      await view(page, 'models-gateway-webhooks-desktop-dark');
      await redraw(page);
      await secret.waitFor();
      await secret.locator('[data-wh-wake-do="done"]').click();
      assert.equal(await secret.count(), 0, 'gone after Done');
      await redraw(page);
      await row.locator('[data-wh-wake-value]').waitFor();
      assert.equal(await row.locator('[data-wh-wake-value]').innerText(), 'https://wake.acme.example/start', 'read back from the server');
      assert.equal(await secret.count(), 0, 'never shown again');
      // A refusal shows the server's words under the input, which keeps what was typed.
      state.wakeFail = {status: 422, code: 'url', detail: 'The wake address starts with https://, has a host, and no spaces, user or password'};
      await row.locator('[data-wh-wake-do="edit"]').click();
      await row.locator('[data-wh-wake-input]').fill('http://wake.acme.example');
      await row.locator('[type="submit"]').click();
      await row.locator('[data-wh-wake-error]').waitFor();
      assert.equal(await row.locator('[data-wh-wake-error]').innerText(), 'The wake address starts with https://, has a host, and no spaces, user or password');
      assert.equal(await row.locator('[data-wh-wake-input]').inputValue(), 'http://wake.acme.example');
      await shot(page, 'wake-error-desktop', GATEWAY);
      state.wakeFail = {status: 409, code: 'gateway', detail: 'Connect the Tico Gateway first.'};
      await row.locator('[type="submit"]').click();
      await page.waitForFunction(() => document.querySelector('[data-wh-wake-error]')?.textContent === 'Connect the Tico Gateway first.');
      await row.locator('[data-wh-wake-do="cancel"]').click();
      assert.equal(await row.locator('[data-wh-wake-error]').count(), 0);
      assert.equal(await row.locator('[data-wh-wake-value]').innerText(), 'https://wake.acme.example/start', 'unchanged by a refusal');
      // Turn off: straight away, no dialog.
      let dialogs = 0;
      page.on('dialog', () => { dialogs++; });
      await row.locator('[data-wh-wake-do="off"]').click();
      await page.waitForFunction(() => document.querySelector('[data-wh-wake-value]')?.textContent === 'Off');
      assert.deepEqual(state.puts.at(-1), {path: '/api/v2/hooks/wake-url', body: {url: null}});
      assert.equal(dialogs, 0);
      assert.equal(await row.locator('[data-wh-wake-do="edit"]').innerText(), 'Set');
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // Phone: the row with its secret, and the edit form, fit.
      const state = server();
      state.relay = {...state.relay, key: 'connected', last_pull: AGO(1), pending: 0};
      state.triggers[0]!.relay = {address_prefix: 'https://gw.tico.dev/v1/hooks/rl_1', created_at: AGO(60), wake: null};
      const {page, errors} = await open(browser, GATEWAY_LINK, {width: 390, height: 844}, state);
      const pane = page.locator(GATEWAY);
      const row = pane.locator('[data-wh-wake-url]');
      await row.locator('[data-wh-wake-do="edit"]').click();
      await row.locator('[data-wh-wake-input]').fill('https://wake.acme.example/start-tico-server-now');
      assert.ok(await noSideways(page), 'wake edit');
      await shot(page, 'wake-edit-phone', GATEWAY);
      await row.locator('[type="submit"]').click();
      await row.locator('[data-wh-secret="wake"]').waitFor();
      assert.ok(await noSideways(page), 'wake secret');
      await shot(page, 'wake-set-phone', GATEWAY);
      await page.locator('#settings-gateway-card').evaluate(el => el.scrollIntoView({block: 'start'}));
      await view(page, 'models-gateway-webhooks-phone-dark');
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // Hidden on a hosted server (HQ wakes it) and from anyone but the owner (403).
      for (const [what, more] of [['hosted', {hosted: true}], ['member', {role: 'member'}]] as [string, Partial<State>][]) {
        const state = Object.assign(server(), more);
        state.relay = {...state.relay, key: 'connected', last_pull: AGO(1), pending: 0};
        const {page, errors} = await open(browser, GATEWAY_LINK, undefined, state);
        const pane = page.locator(GATEWAY);
        await pane.locator('[data-wh-relay-status]').waitFor();
        assert.equal(await pane.locator('[data-wh-wake-url]').count(), 0, `no wake address: ${what}`);
        assert.deepEqual(errors, []);
        await page.close();
      }
    }
    {
      // The endpoint form's other paths: the Messages area, All events, editing a narrowed endpoint, Narrow, and the
      // footer every whDialog shares.
      const state = server();
      state.hooks.push({...state.hooks[0]!, id: 'wh3', name: 'Ops pager', enabled: true, paused_reason: '', events: ['bot.held', 'custom.deploy'],
        filter: {departments: [], bots: [], pipelines: ['engineering']}, headers: [{name: 'x-team', secret: false, value: 'acme'}]});
      const {page, errors} = await open(browser, '#/tools/webhooks', undefined, state);
      const pane = page.locator(OUTGOING), dlg = page.locator('.wh-dialog');
      await pane.locator('[data-wh-hook="wh3"]').waitFor();
      assert.match(await pane.locator('[data-wh-hook="wh3"]').innerText(), /Bot held, custom\.deploy/, 'a code the catalog does not know shows as itself');
      // The Messages area is thread.created and message.*; the page reads "All messages".
      await pane.locator('[data-wh-hook-new]').click();
      await dlg.locator('[name=name]').fill('Chat log');
      await dlg.locator('[name=url]').fill('https://chat.acme.example/in');
      await dlg.locator('[data-ev-choose]').click();
      await dlg.locator('[data-ev-area="messages"] [data-ev-head]').check();
      await dlg.locator('button[type=submit]').click();
      await dlg.waitFor({state: 'detached'});
      await pane.locator('[data-wh-event-facts]').waitFor();
      assert.deepEqual(state.posts.at(-1)!.body.events, ['thread.created', 'message.*']);
      assert.equal((await pane.locator('[data-wh-event-facts]').innerText()).trim(), 'All messages');
      await pane.locator('[data-wh-back]').click();
      // Three ticked, then All events: the boxes clear, the list closes, focus is on Choose, and it saves ['*'].
      await pane.locator('[data-wh-hook-new]').click();
      await dlg.locator('[name=name]').fill('Everything');
      await dlg.locator('[name=url]').fill('https://all.acme.example/in');
      await dlg.locator('[data-ev-choose]').click();
      for (const n of ['task.done', 'bot.held', 'meeting.ready']) await dlg.locator(`[data-ev-name="${n}"]`).check();
      await dlg.locator('[data-ev-back]').click();
      assert.equal(await dlg.locator('[data-ev-pick]').isVisible(), false, 'the list closes');
      assert.equal(await dlg.locator('[data-ev-name]:checked').count(), 0, 'the boxes clear');
      assert.equal(await page.evaluate(() => (document.activeElement as HTMLElement).matches('[data-ev-choose]')), true, 'focus is on Choose');
      assert.equal((await dlg.locator('[data-ev-words]').innerText()).trim(), 'All events');
      // Narrow: three lists side by side, no "All", no inner scroll; the line counts; Send for all undoes it.
      await dlg.locator('[data-filter-narrow]').click();
      const narrowed = dlg.locator('[data-filter-pick]');
      assert.equal(await narrowed.getAttribute('role'), 'group');
      assert.equal(await narrowed.getAttribute('aria-label'), 'Sends for');
      assert.deepEqual(await narrowed.locator('.wh-filter-k').allInnerTexts(), ['Departments', 'Bots', 'Pipelines']);
      assert.equal(new Set(await narrowed.locator('.wh-filter').evaluateAll(cs => cs.map(c => Math.round(c.getBoundingClientRect().top)))).size, 1, 'three columns');
      await narrowed.locator('[data-filter="departments"] input[value="engineering"]').check();
      await narrowed.locator('[data-filter="departments"] input[value="sales"]').check();
      await narrowed.locator('[data-filter="bots"] input[value="ops"]').check();
      assert.doesNotMatch(await narrowed.innerText(), /\bAll\b/);
      assert.deepEqual(await narrowed.locator('.wh-filter-items').evaluateAll(ls => ls.filter(l => l.scrollHeight > l.clientHeight + 1 || getComputedStyle(l).overflowY !== 'visible').length), 0, 'no inner scroll');
      assert.equal((await dlg.locator('[data-filter-words]').innerText()).trim(), '2 departments, 1 bot');
      assert.deepEqual(await dlg.locator('.wh-sum-row').nth(1).locator('button:visible').allInnerTexts(), ['Send for all']);
      if (shots) { await settled(page); await dlg.screenshot({path: path.join(shots, 'outgoing-narrow-desktop.png')}); }
      await dlg.locator('[data-filter-back]').click();
      assert.equal(await narrowed.isVisible(), false);
      assert.equal(await narrowed.locator('input:checked').count(), 0, 'Send for all unticks everything');
      assert.equal(await page.evaluate(() => (document.activeElement as HTMLElement).matches('[data-filter-narrow]')), true, 'focus is on Narrow');
      await dlg.locator('button[type=submit]').click();
      await dlg.waitFor({state: 'detached'});
      const all = state.posts.at(-1)!.body;
      assert.deepEqual(all.events, ['*']);
      assert.equal(all.filter, undefined, 'nothing narrowed sends no filter');
      await pane.locator('[data-wh-back]').click();
      // Edit an endpoint with two events (one the catalog does not know), a pipeline filter and a header: all three
      // sections open, and Save without changes sends what it had.
      await pane.locator('[data-wh-hook="wh3"]').click();
      await pane.locator('[data-wh-hook-edit]').click();
      await dlg.locator('[data-wh-events]').waitFor();
      assert.equal(await dlg.locator('[data-ev-pick]').isVisible(), true);
      assert.equal(await dlg.locator('[data-filter-pick]').isVisible(), true);
      assert.equal(await dlg.locator('[data-hdr]').count(), 1);
      assert.equal(await dlg.locator('[data-ev-name="bot.held"]').isChecked(), true);
      assert.equal(await dlg.locator('[data-ev-area="bots"] [data-ev-head]').evaluate(h => (h as HTMLInputElement).indeterminate), true);
      assert.equal(await dlg.locator('[data-ev-area="other"] [data-ev-name="custom.deploy"]').isChecked(), true, 'an unknown code stays, ticked, under Other');
      assert.equal(await dlg.locator('[data-filter="pipelines"] input[value="engineering"]').isChecked(), true);
      assert.equal((await dlg.locator('[data-ev-words]').innerText()).trim(), 'Bot held, custom.deploy');
      assert.equal((await dlg.locator('[data-filter-words]').innerText()).trim(), '1 pipeline');
      if (shots) { await settled(page); await dlg.screenshot({path: path.join(shots, 'outgoing-edit-open-desktop.png')}); }
      await dlg.locator('button[type=submit]').click();
      await dlg.waitFor({state: 'detached'});
      const kept = state.posts.find(x => x.path === '/api/v2/webhooks/wh3/update')!.body;
      assert.deepEqual(kept.events, ['bot.held', 'custom.deploy']);
      assert.deepEqual(kept.filter, {departments: [], bots: [], pipelines: ['engineering']});
      assert.deepEqual(kept.headers, [{name: 'x-team', value: 'acme', secret: false}]);
      // Every whDialog: title alone in the header, one footer pinned to the bottom, Cancel then the submit (Close when
      // there is none), Signing's Turn off far left.
      await go(page, '#/automations/webhooks/shop');
      const incoming = page.locator(INCOMING);
      await incoming.locator('[data-wh-calls]').waitFor();
      for (const [open, buttons] of [['[data-wh-edit]', ['Cancel', 'Save']], ['[data-wh-sign]', ['Turn off', 'Cancel', 'Save']],
        ['[data-wh-call="d2"] [data-wh-payload]', ['Close']]] as [string, string[]][]) {
        await incoming.locator(open).click();
        await dlg.locator('.wh-foot').waitFor();
        const seen = await dialogLook(page);
        assert.deepEqual(seen.head, ['h2'], open);
        assert.equal(seen.footLast, true, open);
        assert.deepEqual(seen.footButtons, buttons, open);
        assert.equal(seen.footPinned, true, open);
        await dlg.locator('[data-close]').click();
        await dlg.waitFor({state: 'detached'});
      }
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // Phone, 400px: Choose and Narrow open stack in one column, the footer stays in view while the body scrolls, and
      // nothing scrolls sideways.
      const {page, errors} = await open(browser, '#/tools/webhooks', {width: 400, height: 800});
      const pane = page.locator(OUTGOING), dlg = page.locator('.wh-dialog');
      await pane.locator('[data-wh-hook-new]').click();
      await dlg.locator('[data-ev-choose]').click();
      await dlg.locator('[data-filter-narrow]').click();
      const cols = await dlg.evaluate(d => ['.wh-area', '.wh-area-items', '.wh-filters'].map(sel => getComputedStyle(d.querySelector(sel)!).gridTemplateColumns.split(' ').length));
      assert.deepEqual(cols, [1, 1, 1], 'one column each');
      const look = await dialogLook(page);
      assert.equal(look.scrolls, true, 'the body scrolls here');
      assert.equal(look.footPinned, true, 'the footer stays at the bottom');
      await dlg.evaluate(d => { d.scrollTop = d.scrollHeight / 2; });
      assert.equal((await dialogLook(page)).footPinned, true, 'and while scrolled');
      assert.ok(await dlg.evaluate(d => d.scrollWidth <= d.clientWidth + 1), 'nothing sideways');
      if (shots) { await settled(page); await page.screenshot({path: path.join(shots, 'outgoing-new-open-phone.png')}); }
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // Phone: list, detail, outgoing and Gateway with nothing sideways.
      const state = server();
      state.relay = {...state.relay, key: 'connected', last_pull: AGO(1), pending: 0};
      state.triggers[0]!.relay = {address_prefix: 'https://gw.tico.dev/v1/hooks/rl_1', created_at: AGO(60), wake: true};
      state.hosted = true;
      const {page, errors} = await open(browser, '#/automations/webhooks', {width: 390, height: 844}, state);
      let pane = page.locator(INCOMING);
      await pane.locator('[data-wh-open="shop"]').waitFor();
      assert.ok(await noSideways(page), 'list');
      await shot(page, 'incoming-list-phone', INCOMING);
      await view(page, 'automations-incoming-webhooks-phone-dark');
      await pane.locator('[data-wh-open="shop"]').click();
      await pane.locator('[data-wh-calls]').waitFor();
      assert.ok(await noSideways(page), 'detail');
      await shot(page, 'incoming-detail-phone', INCOMING);
      await pane.locator('[data-wh-call="d2"] [data-wh-payload]').click();
      await page.locator('.wh-dialog [data-wh-body]').waitFor();
      if (shots) { await settled(page); await page.screenshot({path: path.join(shots, 'payload-phone.png')}); }
      await page.locator('.wh-dialog [data-close]').click();
      await pane.locator('[data-wh-back]').click();
      await pane.locator('[data-wh-new]').click();
      await page.locator('.wh-dialog [name=name]').waitFor();
      if (shots) { await settled(page); await page.screenshot({path: path.join(shots, 'incoming-new-phone.png')}); }
      await page.locator('.wh-dialog [data-close]').click();
      await go(page, '#/tools/webhooks');
      pane = page.locator(OUTGOING);
      await pane.locator('[data-wh-hook="wh1"]').waitFor();
      assert.ok(await noSideways(page), 'outgoing list');
      await shot(page, 'outgoing-list-phone', OUTGOING);
      await pane.locator('[data-wh-hook-new]').click();
      await page.locator('.wh-dialog [data-wh-events]').waitFor();
      assert.ok(await page.locator('.wh-dialog').evaluate(d => d.scrollWidth <= d.clientWidth + 1), 'the picker fits');
      if (shots) { await settled(page); await page.screenshot({path: path.join(shots, 'outgoing-new-phone.png')}); }
      await page.locator('.wh-dialog [data-close]').click();
      await pane.locator('[data-wh-hook="wh1"]').click();
      await pane.locator('[data-wh-sends]').waitFor();
      assert.ok(await noSideways(page), 'outgoing detail');
      await shot(page, 'outgoing-detail-phone', OUTGOING);
      await pane.locator('[data-wh-hook-edit]').click();
      await page.locator('.wh-dialog [data-wh-headers]').waitFor();
      assert.ok(await page.locator('.wh-dialog').evaluate(d => d.scrollWidth <= d.clientWidth + 1), 'the edit form fits');
      if (shots) { await settled(page); await page.locator('.wh-dialog [data-wh-filters]').scrollIntoViewIfNeeded(); await page.screenshot({path: path.join(shots, 'outgoing-edit-phone.png')}); }
      await page.locator('.wh-dialog [data-close]').click();
      await go(page, GATEWAY_LINK);
      pane = page.locator(GATEWAY);
      await pane.locator('[data-wh-relays]').waitFor();
      assert.equal(await pane.locator('[data-wh-relay="shop"] [data-wh-wake]').isChecked(), true);
      assert.ok(await noSideways(page), 'gateway');
      await shot(page, 'gateway-phone', GATEWAY);
      assert.deepEqual(errors, []);
      await page.close();
    }
  } finally { await browser.close(); }
  console.log('settings-webhooks ok');
})().catch(error => { console.error(error); process.exit(1); });
