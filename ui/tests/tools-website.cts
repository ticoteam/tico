import type * as PageSupport from './support/page.cts';
import type * as Routes from './support/routes.cts';
// A website tool (ui/app/tools-website.ts), offline: the Tools list's website row and Websites filter; Add tool ›
// Website (POST /api/v2/website-tools, then the account, then its grants; a refused account keeps the made tool, so
// Add again does not make a second); the page (#/tools/website/<id>): Accounts with Shared/Own, the 2FA mark and who
// can use it, each row's ⋯ (Show code read again when it runs out, Replace login with a blank password, Who can use
// it, Reveal password · logged, Remove), an account with no username or password (a person signs in by hand), the Browser card (the host read only, Central only), Recent activity, the
// tool's ⋯ (Rename) and the Activity tab's pages; a computer's Browser host switch in Settings. 1440 and 390 px, no
// sideways scroll, light and dark; no stored password or 2FA key is ever on the page before Reveal.
// TICO_UI_SHOTS=<dir> saves a screenshot of each view in each theme and width.
const {chromium}: typeof import('playwright') = require('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const fs: typeof import('node:fs') = require('node:fs');
const path: typeof import('node:path') = require('node:path');
const {html, uiFile} = require('./support/page.cts') as PageSupport.Api;
const {unrouted} = require('./support/routes.cts') as Routes.Api;

interface Who {subject: string; kind: string; id: string; name: string; initials: string}
interface Account {
  id: string; kind: string; username: string; has_password?: boolean; has_totp: boolean; holder: Who | null; access: Who[];
  session: {state: string; where: string | null; computer: string | null; by: Who | null}; can_use: boolean; can_manage: boolean;
}
interface Tool {
  tool: {id: string; name: string; kind: string; sites: string[]; central_only: boolean}; status: {label: string; tone: string};
  accounts: Account[]; host: {runner_id: string; label: string; online: boolean} | null; recent: Row[]; can_manage: boolean; private: boolean;
  can_set_audience: boolean; audience: null; seen_by: {subject: string; kind: string; id: string; name: string; why: string}[];
}
interface Row {id: string; at: string; who: Who; verb: string; page: string; account: string; where: string; result: string}
interface Write {method: string; path: string; body: unknown}

const SECRET = 'hunter2-canary', TOTP = 'JBSWY3DPEHPK3PXP';
const at = (mins: number) => new Date(Date.now() - mins * 60000).toISOString();
const who = (kind: string, id: string, name: string): Who => ({subject: (kind === 'bot' ? 'bot:' : 'human:') + id, kind, id, name,
  initials: name.split(' ').map(w => w[0]).join('').slice(0, 2).toUpperCase()});
const ANA = who('person', 'ana', 'Ana'), BEN = who('person', 'ben', 'Ben'), CARA = who('person', 'cara', 'Cara');
const SUPPORT = who('bot', 'support-bot', 'Support Bot'), OPS = who('bot', 'ops', 'Ops'), COUNSEL = who('bot', 'counsel', 'Counsel');
const EMPS = [SUPPORT, OPS, COUNSEL].map(b => ({id: b.id, name: b.id, display_name: b.name, status: 'active', owner_id: 'ana'}));
const session = (state: string, by: Who | null = null) => ({state, where: state === 'unknown' ? null : 'central', computer: state === 'unknown' ? null : 'Mac mini', by});
const VERBS = ['open', 'sign_in', 'fill', 'copy', 'code', 'control', 'needs_you', 'download'];
const row = (i: number): Row => ({id: 'r' + i, at: at(i * 3), who: [COUNSEL, SUPPORT, ANA][i % 3]!, verb: VERBS[i % VERBS.length]!,
  page: i % 2 ? 'c.tidy.com/homekeepers/219842' : 'c.tidy.com/bookings', account: i % 3 === 1 ? 'shared' : 'own', where: i % 4 ? 'central' : 'own',
  result: i === 4 ? 'refused' : 'use'});

function fake() {
  const tools: Record<string, Tool> = {
    w1: {tool: {id: 'w1', name: 'TIDY admin', kind: 'website', sites: ['c.tidy.com', 'admin.tidy.com'], central_only: false},
      status: {label: 'In use', tone: 'ok'},
      accounts: [
        {id: 'a1', kind: 'shared', username: 'ops@tidy.com', has_totp: true, holder: null, access: [SUPPORT, OPS, BEN, CARA, COUNSEL],
          session: session('in_use'), can_use: true, can_manage: true},
        {id: 'a2', kind: 'own', username: 'ana@tidy.com', has_totp: false, holder: ANA, access: [], session: session('needs_you'), can_use: true, can_manage: true},
        {id: 'a3', kind: 'own', username: 'counsel@bots.tidy.com', has_totp: false, holder: COUNSEL, access: [], session: session('unknown'), can_use: false, can_manage: true}],
      host: {runner_id: 'c1', label: 'Mac mini', online: true}, recent: [0, 1, 2, 3, 4].map(row), can_manage: true, private: false,
      can_set_audience: true, audience: null, seen_by: []},
  };
  const writes: Write[] = [];
  let codes = 0, refuseAccount = 0;
  const machines = [{id: 'c1', label: 'Mac mini', owner_id: 'ana', bots: [], platform: 'darwin', last_seen_at: new Date().toISOString(),
    readiness: {}, screen: 'off', screen_available: false, browser_host: true}];
  const index = () => ({rows: [
    {id: 'api:t-crm', kind: 'api', name: 'Acme CRM', detail: 'api.crm.acme.example', way: 'Proxy', status: {tone: 'ok', text: 'Working'},
      who: [ANA], who_total: 1, needs_you: false, href: '#/tools/api/t-crm'},
    ...Object.values(tools).map(t => ({id: 'website:' + t.tool.id, kind: 'website', name: t.tool.name, detail: t.tool.sites[0], way: 'Browser',
      status: {tone: t.status.tone === 'ok' ? 'ok' : 'wait', text: t.status.label}, who: [SUPPORT, OPS, ANA], who_total: 5, needs_you: false,
      href: '#/tools/website/' + t.tool.id}))],
  counts: {all: 1 + Object.keys(tools).length, needs_you: 0, api: 1, mail: 0, websites: Object.keys(tools).length}, next: null});
  // The hub's answers to what the page reads and writes; undefined: not routed here.
  const answer = (method: string, p: string, body: Record<string, unknown>, url: URL): [number, unknown] | undefined => {
    if (p === '/api/v2/tool-index') return [200, index()];
    if (p === '/api/v2/operations') return [200, {machines, services: []}];
    if (p === '/api/v2/groups') return [200, {groups: []}];
    if (method !== 'GET') writes.push({method, path: p + (url.search ? '?' + [...url.searchParams.keys()].join('&') : ''), body});
    if (p === '/api/v2/runners/c1/browser-host' && method === 'POST') { machines[0]!.browser_host = !!body.on; return [200, {on: body.on}]; }
    if (p === '/api/v2/website-tools' && method === 'POST') {
      const id = 'w' + (Object.keys(tools).length + 1), site = String((body.sites as string[])[0]);
      tools[id] = {tool: {id, name: site, kind: 'website', sites: [site], central_only: !!body.central_only}, status: {label: 'No account', tone: 'waiting'},
        accounts: [], host: null, recent: [], can_manage: true, private: false, can_set_audience: false, audience: null, seen_by: []};
      return [200, {id}];
    }
    const m = /^\/api\/v2\/website-tools\/(w\d)(?:\/(accounts|activity)(?:\/(a\d+)(?:\/(code|access)(?:\/(.+))?)?)?)?$/.exec(p);
    if (!m) {
      const reveal = /^\/api\/v2\/credentials\/(a\d+)\/reveal$/.exec(p);
      return reveal ? [200, {value: SECRET}] : undefined;
    }
    const [, tid = '', part, aid, leaf, subject] = m, t = tools[tid];
    if (!t) return [404, {error: {code: 'not_found', detail: 'No such tool'}}];
    const acct = t.accounts.find(a => a.id === aid);
    if (!part) {
      if (method === 'GET') return [200, structuredClone(t)];
      if (method === 'PATCH') { Object.assign(t.tool, body); return [200, {ok: true}]; }
      if (method === 'DELETE') { delete tools[tid]; return [200, {ok: true}]; }
    }
    if (part === 'activity') {
      const cursor = url.searchParams.get('cursor');
      return [200, cursor ? {rows: [50, 51, 52].map(row), next: null} : {rows: Array.from({length: 50}, (_, i) => row(i)), next: 'p2'}];
    }
    if (part === 'accounts' && !aid && method === 'POST') {
      if (refuseAccount > 0) { refuseAccount--; return [422, {error: {code: 'invalid_request', detail: 'That 2FA key is not valid'}}]; }
      const id = 'a' + (10 + t.accounts.length), holder = typeof body.holder === 'string' ? body.holder : '';
      t.accounts.push({id, kind: holder ? 'own' : 'shared', username: String(body.username ?? ''), has_password: !!body.password, has_totp: !!body.totp, holder: holder ? ANA : null, access: [],
        session: session('unknown'), can_use: true, can_manage: true});
      return [200, {id}];
    }
    if (!acct) return [404, {error: {code: 'not_found', detail: 'No such account'}}];
    if (leaf === 'code') { codes++; return [200, codes === 1 ? {code: '482913', expires_in: 1} : {code: '123456', expires_in: 30}]; }
    if (leaf === 'access' && subject && method === 'PUT') {
      const s = decodeURIComponent(subject), [kind = '', id = ''] = s.split(':');
      acct.access = body.on ? [...acct.access, who(kind === 'bot' ? 'bot' : 'person', id, id)] : acct.access.filter(w => w.subject !== s);
      return [200, {ok: true}];
    }
    if (!leaf && method === 'PATCH') { if (typeof body.username === 'string') acct.username = body.username; return [200, {ok: true}]; }
    if (!leaf && method === 'DELETE') { t.accounts = t.accounts.filter(a => a !== acct); return [200, {ok: true}]; }
    return undefined;
  };
  return {tools, writes, answer, machines, refuse: (n: number) => { refuseAccount = n; }, codes: () => codes};
}

const sideways = (page: import('playwright').Page) => page.evaluate(() => [document.documentElement, document.querySelector('#main')].some(el => el && el.scrollWidth > el.clientWidth + 1));
const shots: string[] = [];
async function shot(page: import('playwright').Page, name: string) {
  const dir = process.env.TICO_UI_SHOTS;
  if (!dir) return;
  fs.mkdirSync(dir, {recursive: true});
  for (const theme of ['light', 'dark']) {
    await page.evaluate((t: string) => document.documentElement.setAttribute('data-theme', t), theme);
    await page.evaluate(() => Promise.all(document.getAnimations()
      .filter(a => a.effect?.getComputedTiming().endTime !== Infinity).map(a => a.finished.catch(() => {}))));
    const file = path.join(dir, `website-${name}-${theme}.png`);
    await page.screenshot({animations: 'disabled', path: file, fullPage: true});
    shots.push(file);
  }
  await page.evaluate(() => document.documentElement.removeAttribute('data-theme'));
}

async function start(browser: import('playwright').Browser, hash: string, width: number, frame: Buffer) {
  const page = await browser.newPage({viewport: {width, height: width < 500 ? 844 : 900}, serviceWorkers: 'block'});
  const errors: string[] = [];
  page.on('pageerror', (e: Error) => errors.push(e.message));
  const f = fake();
  await page.route('**/*', route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname, method = req.method();
    const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1]!)))
      return route.fulfill({contentType: ui[1]!.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]!), 'utf8')});
    if (/\.(?:js|ts)$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: ''});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const config = {version: '0.1.0', app_name: 'Tico'};
    if (p === '/api/me') return json({id: 'ana', role: 'owner', name: 'Ana', email: 'ana@example.com', cloud: true, registered: true, credential_access: true, config});
    if (p === '/api/v2/config') return json(config);
    if (p === '/api/employees') return json(EMPS);
    if (p === '/api/humans' || p === '/api/people') return json({people: [{id: 'ana', name: 'Ana'}, {id: 'ben', name: 'Ben'}, {id: 'cara', name: 'Cara'}], teams: {}});
    if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/needs-you') return json({items: []});
    if (/\/browser\/frame$/.test(p)) return route.fulfill({contentType: 'image/jpeg', headers: {'X-Page-Url': 'https://c.tidy.com/login?next=%2F', 'X-Page-Width': '1280', 'X-Page-Height': '800'}, body: frame});
    if (/\/browser(?:\/control|\/input)?$/.test(p) && method === 'POST') return json({ok: true, where: 'central'});
    if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    let body: Record<string, unknown> = {};
    try { body = req.postDataJSON() ?? {}; } catch { body = {}; }
    const got = f.answer(method, p, body, url);
    if (got) return json(got[1], got[0]);
    return json(unrouted(p));
  });
  await page.goto('https://tico-ui.test/' + hash);
  return {page, errors, f};
}
// A JPEG of a small sign-in page, as the browser host would send it.
async function frameJpeg(browser: import('playwright').Browser): Promise<Buffer> {
  const p = await browser.newPage({viewport: {width: 1280, height: 800}});
  await p.setContent(`<body style="margin:0;font:16px system-ui;background:#f6f7f9;display:grid;place-items:center;height:100vh">
    <form style="width:360px;padding:32px;background:#fff;border:1px solid #dde;border-radius:12px;display:grid;gap:12px">
    <h2 style="margin:0">TIDY admin</h2><input placeholder="Email" style="height:40px;padding:0 10px"><input placeholder="Password" type="password" style="height:40px;padding:0 10px">
    <button style="height:40px;background:#1a73e8;color:#fff;border:0;border-radius:6px">Sign in</button></form></body>`);
  const buf = await p.screenshot({type: 'jpeg', quality: 70});
  await p.close();
  return buf;
}
const menuItems = (page: import('playwright').Page) => page.locator('#tw-menu:popover-open .tl-mi').evaluateAll(b => b.map(x => x.textContent));
async function menu(page: import('playwright').Page, account: string, item: string) {
  await page.locator(`[data-tw-acct-menu="${account}"]`).click();
  await page.locator('#tw-menu:popover-open .tl-mi', {hasText: item}).click();
}
const dialogNamed = (page: import('playwright').Page, name: string) => page.locator(`dialog[open][aria-label="${name}"]`);

const last = (f: ReturnType<typeof fake>, method: string, pathStart: string) => [...f.writes].reverse().find(w => w.method === method && w.path.startsWith(pathStart));

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    const frame = await frameJpeg(browser);
    for (const width of [1440, 390]) {
      const tag = width < 500 ? 'phone' : 'desktop';
      // The list: the website row, and the Websites filter that shows only it.
      {
        const {page, errors, f} = await start(browser, '#/tools', width, frame);
        await page.locator('tr[data-tl-row="website:w1"]').waitFor();
        await page.locator('[data-tl-show="websites"]').click();
        assert.deepEqual(await page.locator('tr[data-tl-row]').evaluateAll(r => r.map(x => x.dataset.tlRow)), ['website:w1'], `${tag}: Websites`);
        assert.equal(await sideways(page), false, `${tag}: the list has no sideways scroll`);
        await shot(page, `list-${tag}`);
        // Add tool › Website. The account is refused once: the tool made first is kept, so Add again only adds the account.
        await page.locator('[data-tl-add] summary').click();
        await page.locator('[data-tl-add-item="website"]').click();
        const add = dialogNamed(page, 'Add website');
        await add.waitFor();
        await add.locator('[name=site]').fill('app.acme.example');
        await add.locator('[name=username]').fill('ops@acme.example');
        await add.locator('[name=password]').fill(SECRET);
        await add.locator('[name=totp]').fill(TOTP);
        await add.locator('[data-tw-who-pick]').selectOption('bot:support-bot');
        await add.locator('[data-tw-who-pick]').selectOption('human:ben');
        await add.locator('[data-tw-unpick="human:ben"]').click();
        assert.deepEqual(await add.locator('[data-tw-chip]').evaluateAll(c => c.map(x => x.getAttribute('data-tw-chip'))), ['bot:support-bot'], `${tag}: one picked`);
        await add.locator('[name=central_only]').check();
        assert.equal(await sideways(page), false, `${tag}: Add website has no sideways scroll`);
        await shot(page, `add-${tag}`);
        f.refuse(1);
        await add.locator('button[type=submit]').click();
        await add.locator('[data-wh-error]', {hasText: 'That 2FA key is not valid'}).waitFor();
        assert.ok(!(await add.locator('[data-wh-error]').innerText()).includes(TOTP), `${tag}: the refusal does not echo the key`);
        await add.locator('button[type=submit]').click();
        await page.waitForFunction(() => location.hash === '#/tools/website/w2');
        assert.deepEqual(f.writes.filter(w => w.path === '/api/v2/website-tools'), [{method: 'POST', path: '/api/v2/website-tools', body: {sites: ['app.acme.example'], central_only: true}}],
          `${tag}: one tool made`);
        assert.deepEqual(last(f, 'POST', '/api/v2/website-tools/w2/accounts')?.body, {username: 'ops@acme.example', password: SECRET, totp: TOTP}, `${tag}: the account`);
        assert.deepEqual(last(f, 'PUT', '/api/v2/website-tools/w2/accounts/a10/access/')?.path, '/api/v2/website-tools/w2/accounts/a10/access/bot%3Asupport-bot', `${tag}: its grant`);
        assert.deepEqual(last(f, 'PUT', '/api/v2/website-tools/w2/accounts/a10/access/')?.body, {on: true});
        await page.locator('#tool-website [data-tw-account="a10"]').waitFor();
        assert.equal(await page.locator('dialog[open]').count(), 0, `${tag}: the dialog is gone`);
        assert.deepEqual(errors, [], `${tag}: no page errors`);
        await page.close();
      }
      // The page: accounts, browser, recent activity, who can see it.
      {
        const {page, errors, f} = await start(browser, '#/tools/website/w1', width, frame);
        const root = page.locator('#tool-website');
        await root.locator('[data-tw-account="a1"]').waitFor();
        assert.equal(await root.locator('.tt-title h1').innerText(), 'TIDY admin');
        assert.match(await root.locator('.tt-title .tt-status').innerText(), /In use/);
        assert.deepEqual(await root.locator('.tw-acct').evaluateAll(r => r.map(x => [x.getAttribute('data-kind'), x.querySelector('.tw-acct-text')?.textContent])),
          [['shared', 'ops@tidy.com'], ['own', 'Ana'], ['own', 'Counsel']], `${tag}: accounts, a shared one by username, an own one by holder`);
        assert.equal(await root.locator('[data-tw-account="a1"] [data-tw-2fa]').count(), 1, `${tag}: 2FA mark`);
        assert.equal(await root.locator('[data-tw-account="a2"] [data-tw-2fa]').count(), 0);
        assert.equal(await root.locator('[data-tw-account="a1"] .tt-face').count(), 4, `${tag}: four faces`);
        assert.equal(await root.locator('[data-tw-account="a1"] .tt-more').innerText(), '+1');
        assert.equal(await root.locator('[data-tw-account="a2"] .tw-acct-user').innerText(), 'ana@tidy.com');
        assert.equal(await root.locator('[data-tw-account="a2"] .tw-ses').getAttribute('data-state'), 'needs_you');
        assert.match(await root.locator('#tool-browser .tw-host').innerText(), /Mac mini/);
        assert.equal(await root.locator('#tool-browser .tw-host').getAttribute('data-online'), 'true');
        assert.equal(await root.locator('#tool-recent .tw-rec').count(), 5, `${tag}: five recent`);
        assert.equal(await root.locator('#tool-see').count(), 1, `${tag}: Who can see it`);
        assert.equal(await root.locator('[data-tw-chat], [data-ta-key-add]').count(), 0, `${tag}: no Chat, no API card`);
        assert.ok(!(await page.content()).includes(SECRET) && !(await page.content()).includes(TOTP), `${tag}: no stored secret on the page`);
        assert.equal(await sideways(page), false, `${tag}: the page has no sideways scroll`);
        await shot(page, `page-${tag}`);
        // An account's ⋯: everything for a shared account this person manages.
        await root.locator('[data-tw-acct-menu="a1"]').click();
        assert.deepEqual(await menuItems(page), ['Show code', 'Open browser', 'Replace login', 'Who can use it', 'Reveal password · logged', 'Remove'], `${tag}: the ⋯ menu`);
        await shot(page, `menu-${tag}`);
        await page.keyboard.press('Escape');
        await root.locator('[data-tw-acct-menu="a3"]').click();
        assert.deepEqual(await menuItems(page), ['Replace login', 'Reveal password · logged', 'Remove'], `${tag}: a bot's own account`);
        await page.keyboard.press('Escape');
        // Show code: read again when it runs out.
        await menu(page, 'a1', 'Show code');
        const code = dialogNamed(page, 'Code');
        await code.locator('[data-tw-code]', {hasText: '482 913'}).waitFor();
        await code.locator('[data-tw-code]', {hasText: '123 456'}).waitFor({timeout: 5000});
        assert.equal(f.codes(), 2, `${tag}: read again once`);
        await shot(page, `code-${tag}`);
        await code.locator('[data-close]').click();
        // Replace login: the password field is blank; only what changed is sent.
        await menu(page, 'a1', 'Replace login');
        const replace = dialogNamed(page, 'Replace login');
        assert.equal(await replace.locator('[name=password]').inputValue(), '', `${tag}: no stored password`);
        assert.equal(await replace.locator('[name=totp]').inputValue(), '', `${tag}: no stored 2FA key`);
        assert.equal(await replace.locator('[name=username]').inputValue(), 'ops@tidy.com');
        await replace.locator('[name=password]').fill('new-' + SECRET);
        await replace.locator('button[type=submit]').click();
        await replace.waitFor({state: 'detached'});
        assert.deepEqual(last(f, 'PATCH', '/api/v2/website-tools/w1/accounts/a1'), {method: 'PATCH', path: '/api/v2/website-tools/w1/accounts/a1', body: {password: 'new-' + SECRET}});
        // Remove 2FA.
        await menu(page, 'a1', 'Replace login');
        await replace.locator('[name=no_totp]').check();
        await replace.locator('button[type=submit]').click();
        await replace.waitFor({state: 'detached'});
        assert.deepEqual(last(f, 'PATCH', '/api/v2/website-tools/w1/accounts/a1')?.body, {totp: null}, `${tag}: Remove 2FA`);
        // Who can use it: each change saved at once.
        await menu(page, 'a1', 'Who can use it');
        const access = dialogNamed(page, 'Who can use it');
        assert.equal(await access.locator('[data-tw-access-row]').count(), 5);
        await access.locator('[data-tw-revoke="human:cara"]').click();
        await access.locator('[data-tw-access-row="human:cara"]').waitFor({state: 'detached'});
        assert.deepEqual(last(f, 'PUT', '/api/v2/website-tools/w1/accounts/a1/access/'), {method: 'PUT', path: '/api/v2/website-tools/w1/accounts/a1/access/human%3Acara', body: {on: false}});
        await access.locator('[data-tw-access-pick]').selectOption('human:ana');
        await access.locator('[data-tw-access-row="human:ana"]').waitFor();
        assert.deepEqual(last(f, 'PUT', '/api/v2/website-tools/w1/accounts/a1/access/')?.body, {on: true});
        await shot(page, `access-${tag}`);
        await access.locator('[data-close]').click();
        // Reveal password · logged: the vault's read, people only.
        await menu(page, 'a1', 'Reveal password · logged');
        const reveal = dialogNamed(page, 'Password');
        await reveal.locator('[data-tw-revealed]').waitFor();
        assert.equal(await reveal.locator('[data-tw-revealed]').inputValue(), SECRET);
        assert.deepEqual(last(f, 'POST', '/api/v2/credentials/')?.path, '/api/v2/credentials/a1/reveal');
        await reveal.locator('[data-close]').click();
        // Remove: confirmed, then gone.
        await menu(page, 'a3', 'Remove');
        await page.locator('dialog.settings-confirm [data-confirm-ok]').click();
        await root.locator('[data-tw-account="a3"]').waitFor({state: 'detached'});
        assert.deepEqual(last(f, 'DELETE', '/api/v2/website-tools/w1/accounts/'), {method: 'DELETE', path: '/api/v2/website-tools/w1/accounts/a3', body: {}});
        // An account a person signs in to by hand: no username, no password. Named by the tool, nothing to reveal, and
        // Replace login adds a password later.
        await root.locator('[data-tw-acct-add]').click();
        const bare = dialogNamed(page, 'Add account');
        await bare.locator('button[type=submit]').click();
        await root.locator('[data-tw-account="a12"]').waitFor();
        assert.deepEqual(last(f, 'POST', '/api/v2/website-tools/w1/accounts')?.body, {}, `${tag}: no username, no password`);
        assert.equal(await root.locator('[data-tw-account="a12"] .tw-acct-text').innerText(), 'TIDY admin', `${tag}: named by the tool`);
        await root.locator('[data-tw-acct-menu="a12"]').click();
        assert.deepEqual(await menuItems(page), ['Open browser', 'Replace login', 'Who can use it', 'Remove'], `${tag}: no Reveal without a password`);
        await page.keyboard.press('Escape');
        await menu(page, 'a12', 'Replace login');
        assert.equal(await replace.locator('[name=password]').getAttribute('placeholder'), null, `${tag}: no "Unchanged" for a password never stored`);
        await replace.locator('[name=password]').fill('later-' + SECRET);
        await replace.locator('button[type=submit]').click();
        await replace.waitFor({state: 'detached'});
        assert.deepEqual(last(f, 'PATCH', '/api/v2/website-tools/w1/accounts/a12')?.body, {password: 'later-' + SECRET}, `${tag}: a password added later`);
        // Central only.
        await root.locator('[data-tw-central]').check();
        await page.waitForFunction(() => !document.querySelector<HTMLInputElement>('[data-tw-central]')?.disabled);
        assert.deepEqual(last(f, 'PATCH', '/api/v2/website-tools/w1')?.body, {central_only: true}, `${tag}: Central only`);
        // The tool's ⋯: Rename.
        await root.locator('[data-tw-tool-menu]').click();
        assert.deepEqual(await menuItems(page), ['Rename', 'Sites', 'Delete'], `${tag}: the tool's ⋯`);
        await page.locator('#tw-menu .tl-mi', {hasText: 'Rename'}).click();
        await dialogNamed(page, 'Rename').locator('[name=name]').fill('TIDY');
        await dialogNamed(page, 'Rename').locator('button[type=submit]').click();
        await root.locator('.tt-title h1', {hasText: /^TIDY$/}).waitFor();
        assert.deepEqual(last(f, 'PATCH', '/api/v2/website-tools/w1')?.body, {name: 'TIDY'});
        assert.equal(await sideways(page), false, `${tag}: still no sideways scroll`);
        // Activity: 50, then More.
        await root.locator('[data-tw-tab="activity"]').click();
        await root.locator('.tw-log tbody tr').first().waitFor();
        assert.equal(await root.locator('.tw-log tbody tr').count(), 50, `${tag}: a page of 50`);
        assert.match(await root.locator('.tw-log tbody tr').first().innerText(), /Counsel[\s\S]*opened[\s\S]*c\.tidy\.com\/bookings[\s\S]*own browser/);
        assert.equal(await root.locator('.tw-log [data-result="refused"]').count(), 1);
        await root.locator('[data-tw-more-btn]').click();
        await page.waitForFunction(() => document.querySelectorAll('.tw-log tbody tr').length === 53);
        assert.equal(await root.locator('[data-tw-more]').isHidden(), true, `${tag}: no More at the end`);
        assert.equal(await sideways(page), false, `${tag}: Activity has no sideways scroll`);
        await shot(page, `activity-${tag}`);
        assert.ok(!f.writes.some(w => JSON.stringify(w).includes(TOTP)), 'no 2FA key written on this page');
        assert.deepEqual(errors, [], `${tag}: no page errors`);
        await page.close();
      }
    }
    // Settings › Computers: an admin's Browser host switch.
    {
      const {page, errors, f} = await start(browser, '#/settings', 1440, frame);
      await page.locator('[data-settings-tab=devices]').click();
      await page.locator('#set-machines .machine-name[data-computer-open=c1]').click();
      const box = page.locator('#set-machines dialog[open] [data-browser-host=c1]');
      await box.waitFor();
      assert.equal(await box.isChecked(), true, 'the browser host is on');
      await box.uncheck();
      await page.waitForFunction(() => document.querySelector<HTMLInputElement>('[data-browser-host=c1]')?.checked === false
        && !document.querySelector<HTMLInputElement>('[data-browser-host=c1]')?.disabled);
      assert.deepEqual(last(f, 'POST', '/api/v2/runners/c1/browser-host')?.body, {on: false});
      assert.equal(f.machines[0]!.browser_host, false);
      await page.locator('#set-machines dialog[open] [data-detail-section=settings]').scrollIntoViewIfNeeded();
      await shot(page, 'computer-desktop');
      assert.deepEqual(errors, [], 'settings: no page errors');
      await page.close();
    }
    if (shots.length) console.log(`screenshots:\n${shots.join('\n')}`);
    console.log('tools-website: ok');
  } finally {
    await browser.close();
  }
})().catch((e: unknown) => { console.error(e); process.exit(1); });
