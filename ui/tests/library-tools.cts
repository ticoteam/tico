// A tool's own page and a key's (ui/app/redesign-library.ts), and the Tools list's rows for them (ui/app/tools.ts),
// against fixtures/redesign-tools.json (the Acme example, shaped as GET /api/v2/tools, /api/v2/tools/{service},
// /api/v2/credentials and /api/v2/groups answer). Keys was a page of its own; it is folded into Tools: the list shows
// the tools bots declare (In use when someone has one, else Available; a pill when one has an issue) and each key no
// tool holds (Kind › Keys), Who has access narrows by a bot, a person or a team with the teams under it. A row opens
// the tool: its issues, who has it (by team, each with what they may change there), its keys (set or not, when a bot
// last used one, Rotate / Access / Reveal / Add), then the page, its queries and learnings. A key no tool names has its
// own page. The old addresses (#/tools/keys…, #/library/connections/<tool>, #/integrations, #/credentials) lead to
// Tools. A member who cannot open the vault never reads it. No secret value is ever on the page.
// Phone first (390×844), then a computer. TICO_UI_SHOTS=<dir> saves screenshots.
import type {Browser, Page, Route} from 'playwright';
import type * as PageSupport from './support/page.cts';
import type * as RouteSupport from './support/routes.cts';
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cts') as PageSupport.Api;
const {unrouted} = require('./support/routes.cts') as RouteSupport.Api;
declare const S: {me: {id: string} | null; emps: unknown[] | null};
interface Me {id: string; name: string; email: string; role: string; company_role: string; cloud: boolean; credential_access: boolean; bot_admin?: boolean}
interface Grant {id: string; subject: string; granted_by: string; parent_id: null; can_revoke: boolean}
interface Cred {id: string; name: string; username: string; kind: string; env: string; preview: string; source: string; revision: number; created_at: string; updated_at: string; stored: boolean; can_reveal: boolean; last_used_at: string | null; last_used_by: string; grants: Grant[]}
interface Seen {errors: string[]; writes: {method: string; path: string; body: string}[]; vaultReads: number; vault: {credentials: Cred[]}}
const uiDir = path.join(__dirname, '..');
const fixture = (name: string) => JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', name), 'utf8'));
const FX = fixture('redesign-tools.json');
const roster = fixture('redesign-roster.json');
const PHONE = {width: 390, height: 844}, DESKTOP = {width: 1440, height: 900};
const LIST = '#/tools';
const SECRET = 'ghp-secret-value-5c4b3a';
const OWNER: Me = {id: 'ana', name: 'Ana Silva', email: 'ana@acme.example', role: 'owner', company_role: 'owner', cloud: true, credential_access: true, bot_admin: true};
const MEMBER: Me = {id: 'cara', name: 'Cara Diaz', email: 'cara@acme.example', role: 'human', company_role: 'member', cloud: true, credential_access: false};

async function open(browser: Browser, {viewport = PHONE, hash = LIST, me = OWNER, tools = FX.tools.integrations, listGate = null, emps = roster.employees, groups = FX.groups}: {viewport?: {width: number; height: number}; hash?: string; me?: Me; tools?: unknown[]; listGate?: Promise<unknown> | null; emps?: unknown[]; groups?: unknown[]} = {}) {
  const page = await browser.newPage({viewport, serviceWorkers: 'block', hasTouch: viewport === PHONE});
  const seen: Seen = {errors: [], writes: [], vaultReads: 0, vault: JSON.parse(JSON.stringify(FX.vault))};
  page.on('pageerror', (e: Error) => seen.errors.push(e.message));
  const config = {company_name: 'Acme'};
  await page.route('**/*', async (route: Route) => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname, method = req.method();
    const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (url.origin !== 'http://tico-ui.test') return route.abort();
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const file = p.startsWith('/tico/ui/') ? uiFile(p.slice(9)) : p.startsWith('/vendor/') ? path.join(uiDir, p) : '';
    if (file && fs.existsSync(file)) return route.fulfill({contentType: /\.(?:js|ts)$/.test(file) ? 'application/javascript' : file.endsWith('.css') ? 'text/css' : file.endsWith('.woff2') ? 'font/woff2' : 'application/octet-stream', body: fs.readFileSync(file)});
    if (p === '/api/v2/events' || p.endsWith('/watch')) return;
    if (method !== 'GET') seen.writes.push({method, path: p, body: req.postData() || ''});
    if (p === '/api/v2/config') return json(config);
    if (p === '/api/me') return json({...me, config});
    if (p === '/api/employees') return json(emps);
    if (p === '/api/humans') return json(roster.humans);
    if (p === '/api/status') return json({active: [], employees: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/groups') return json(groups);
    if (p === '/api/v2/tool-index') return json({rows: [], counts: {}, next: null});
    const m = p.match(/^\/api\/v2\/(?:tools|integrations)(?:\/([^/]+))?$/);
    if (m && !m[1]) { await listGate; return json({label: 'Available tools', integrations: tools}); }
    if (m) {
      const one = FX.pages[decodeURIComponent(m[1]!)];
      return one ? json(one) : json({error: {code: 'not_found', detail: 'No tool named ' + m[1]}}, 404);
    }
    if (p === '/api/v2/credentials' && method === 'GET') {
      if (!me.credential_access) return json({error: {code: 'forbidden', detail: 'No'}}, 403);
      seen.vaultReads++;
      return json(seen.vault);
    }
    if (p === '/api/v2/credentials' && method === 'POST') {
      const body = JSON.parse(req.postData()!);
      seen.vault.credentials.push({id: 'c9', name: body.name, username: body.username, kind: body.kind, env: body.env, preview: 'ghp-••••3a',
        source: body.source, revision: 1, created_at: '2026-10-28T09:00:00Z', updated_at: '2026-10-28T09:00:00Z', stored: true, can_reveal: true,
        last_used_at: null, last_used_by: '', grants: []});
      return json({id: 'c9', name: body.name});
    }
    const grant = p.match(/^\/api\/v2\/credentials\/([^/]+)\/grants$/);
    if (grant) {
      const cred = seen.vault.credentials.find(c => c.id === grant[1])!;
      cred.grants.push({id: 'g9', subject: JSON.parse(req.postData()!).subject, granted_by: 'human:ana', parent_id: null, can_revoke: true});
      return json({id: 'g9'});
    }
    return json(unrouted(p));
  });
  await page.goto('http://tico-ui.test/' + hash);
  await page.waitForFunction(() => typeof S !== 'undefined' && S.me && (S.emps || []).length);
  return {page, seen};
}
const sideways = (page: Page) => page.evaluate(() => [document.documentElement, document.querySelector('#main')].some(el => el && el.scrollWidth > el.clientWidth + 1));
const hashSoon = (page: Page, want: string) => page.waitForFunction(w => location.hash === w, want).then(() => true, () => false);
async function shot(page: Page, name: string) {
  const dir = process.env.TICO_UI_SHOTS;
  if (!dir) return;
  fs.mkdirSync(dir, {recursive: true});
  await page.screenshot({animations: 'disabled', path: path.join(dir, name + '.png')});
}
// [key, name, pill, how many faces]
const rows = (page: Page) => page.locator('tr[data-tl-row]').evaluateAll(els => els.map(el => [(el as HTMLElement).dataset.tlRow,
  el.querySelector<HTMLElement>('.au-nm')!.innerText.trim(), el.querySelector('.tt-pill')?.textContent || '', el.querySelectorAll('.tt-face').length]));
const keys = (page: Page) => page.locator('tr[data-tl-row]').evaluateAll(els => els.map(el => (el as HTMLElement).dataset.tlRow));
const ROWS = [
  ['service:close-crm', 'Close CRM', 'Problem', 3],
  ['service:acme-ops', 'Acme Ops MCP', '', 1],
  ['service:postgres', 'Acme Postgres', '', 1],
  ['credentials/c3', 'OpenAI key', '', 1],
  ['service:mail', 'Mail', 'No key', 0],
  ['service:aside', 'Aside browser', '', 0],
  ['service:github', 'GitHub', '', 0],
];
const noSecret = async (page: Page, tag: string) => assert.ok(!(await page.content()).includes(SECRET), tag + ': no secret value on the page');
// A filter in the rail; a phone opens the filters and the section first.
async function pick(page: Page, sec: string, sel: string) {
  if (!(await page.locator(sel).isVisible().catch(() => false))) {
    if (!(await page.locator('#tools-root.filters-open').count())) await page.locator('[data-tl-filters]').click();
    if (!(await page.locator(sel).count())) await page.locator(`[data-tl-fsec="${sec}"]`).click();
  }
  await page.locator(sel).click();
}

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    // A phone: the list, the filters, a tool, its access and keys, back.
    {
      const tag = 'phone';
      const {page, seen} = await open(browser);
      await page.locator('tr[data-tl-row]').first().waitFor();
      assert.deepEqual(await rows(page), ROWS, tag + ': the declared tools and the loose key, each with its pill and faces');
      assert.deepEqual(await page.locator('tr[data-tl-grp]').evaluateAll(g => g.map(x => (x as HTMLElement).dataset.tlGrp)), ['In use', 'Available'], tag + ': grouped');
      assert.equal(await page.locator('tr[data-tl-row="service:old-wiki"]').count(), 0, tag + ': a removed tool is not listed');
      assert.equal(await page.locator('#side a[data-nav="tools"].cur').count(), 1, tag + ': Tools is current');
      assert.match(await page.locator('#side a[data-nav="tools"]').innerText(), /^(api\s*)?Tools$/, tag + ': the nav says Tools');
      assert.equal(await page.locator('#account-menu [href="#/integrations"]').count(), 0, tag + ': no second Tools in the account menu');
      assert.equal(await sideways(page), false, tag + ': no sideways scroll');
      await shot(page, 'phone-tools');

      // Who has access: a team counts the teams under it; a bot; a person, by a key granted to her; a team's keys too.
      await pick(page, 'who', '[data-tl-who="group:eng"]');
      assert.deepEqual(await keys(page), ['service:close-crm', 'service:acme-ops', 'service:postgres'], tag + ': a team counts the teams under it');
      assert.ok(await hashSoon(page, LIST + '?who=group%3Aeng'), tag + ': the filter is in the address');
      await pick(page, 'who', '[data-tl-who="bot:builder"]');
      assert.deepEqual(await keys(page), ['service:postgres'], tag + ': one bot');
      await pick(page, 'who', '[data-tl-who="human:ana"]');
      assert.deepEqual(await keys(page), ['service:close-crm'], tag + ': a person, by a key granted to her');
      await pick(page, 'who', '[data-tl-who="group:ops"]');
      assert.deepEqual(await keys(page), ['service:close-crm', 'credentials/c3'], tag + ': a team, its keys too');
      await pick(page, 'who', '[data-tl-who="group:ops"]');
      await pick(page, 'needs', '[data-tl-fneeds]');
      assert.deepEqual(await keys(page), ['service:close-crm', 'service:mail'], tag + ': Needs you');
      await page.locator('[data-tl-q]').fill('mail');
      assert.deepEqual(await keys(page), ['service:mail'], tag + ': search within them');
      await page.locator('[data-tl-q]').fill('');
      await pick(page, 'needs', '[data-tl-fneeds]');
      await pick(page, 'kind', '[data-tl-show="keys"]');
      assert.deepEqual(await keys(page), ['credentials/c3'], tag + ': Keys');
      await pick(page, 'kind', '[data-tl-show=""]');
      assert.equal(await page.locator('tr[data-tl-row]').count(), ROWS.length, tag + ': all again');
      await page.locator('[data-tl-filters]').click();

      await page.locator('tr[data-tl-row="service:close-crm"] .au-nm').click();
      assert.ok(await hashSoon(page, LIST + '/close-crm'), tag + ': its own address');
      await page.locator('#int-detail h1').filter({hasText: 'Close CRM'}).waitFor();
      // In order: issues, access, keys, then the page.
      const order = await page.locator('#int-detail .card').evaluateAll(cs => cs.map(c => (c as HTMLElement).className.split(' ').find(x => x.startsWith('lc-')) || c.id || 'meta'));
      assert.deepEqual(order.slice(0, 4), ['lc-issues-card', 'lc-access', 'lc-creds', 'meta'], tag + ': issues, access, keys, then the page');
      assert.equal(await page.locator('.lc-creds h2').innerText(), 'Keys', tag + ': the keys are the tool\'s');
      assert.match(await page.locator('.lc-issues-card').innerText(), /Credential missing on Test Mac · Release Owner/);
      const access = await page.locator('.lc-access .lc-team').evaluateAll((ts: Element[]) => ts.map(t => [t.querySelector('summary span')!.textContent,
        [...(t.querySelectorAll('.lc-who-row') as unknown as Iterable<Element>)].map(r => [r.querySelector('.lc-wn')!.textContent, r.querySelector('.pill')?.textContent || ''])]));
      assert.deepEqual(access, [
        ['Engineering', [['Engineering Lead', 'Approval']]],
        ['Operations', [['Ana Silva', 'Approval'], ['Release Owner', 'Read only']]],
      ], tag + ': who has it, by team, each with what they may change');
      assert.equal(await page.locator('.lc-access .lc-who-row .lc-dot').count(), 1, tag + ': the bot with the problem is marked');
      const cred = await page.locator('.lc-creds .lc-cred').innerText();
      assert.match(cred, /Close CRM/); assert.match(cred, /CLOSE_API_KEY/); assert.match(cred, /Set/); assert.match(cred, /used .* by Engineering Lead/);
      assert.deepEqual(await page.locator('.lc-creds .lc-acts button').allInnerTexts(), ['Rotate', 'Access', 'Reveal'], tag + ': the vault\'s actions');
      assert.equal(await page.locator('[data-learning-count]').innerText(), '2', tag + ': the learnings counted');
      assert.equal(await page.locator('#int-learn-add').count(), 1, tag + ': a learning can be added');
      assert.equal(await page.getAttribute('#int-body a[href^="#/"]', 'href'), LIST + '/postgres', tag + ': a sibling page stays in Tools');
      assert.equal(await sideways(page), false, tag + ': the page fits');
      await shot(page, 'phone-tool');

      await page.locator('.tt-crumb a[href="#/tools"]').click();
      assert.ok(await hashSoon(page, LIST), tag + ': back to the list');
      await page.locator('tr[data-tl-row]').first().waitFor();
      await noSecret(page, tag);
      assert.deepEqual(seen.writes, [], tag + ': nothing written');
      assert.deepEqual(seen.errors, [], tag + ': no page errors');
      await page.close();
    }

    // A phone: a tool whose key has no value, and one whose variable the vault does not hold: Add, prefilled.
    {
      const tag = 'phone add';
      const {page, seen} = await open(browser, {hash: LIST + '/mail'});
      await page.locator('#int-detail h1').filter({hasText: 'Mail'}).waitFor();
      assert.match(await page.locator('.lc-issues-card').innerText(), /Mail password: no value stored/);
      assert.match(await page.locator('.lc-creds .lc-cred .pill').innerText(), /No value/);
      assert.deepEqual(await page.locator('.lc-creds .lc-acts button').allInnerTexts(), ['Set', 'Access'], tag + ': no value to reveal');
      assert.match(await page.locator('.lc-access').innerText(), /Nobody has it/);

      await page.evaluate(h => { location.hash = h; }, LIST + '/github');
      await page.locator('#int-detail h1').filter({hasText: 'GitHub'}).waitFor();
      await page.locator('.lc-connect h2').filter({hasText: 'GitHub App'}).waitFor();
      const missing = page.locator('.lc-creds .lc-cred');
      assert.match(await missing.innerText(), /GH_TOKEN/);
      assert.match(await missing.locator('.pill').innerText(), /Not set/);
      await shot(page, 'phone-tool-missing');
      await missing.locator('[data-lc-add-env]').click();
      const form = page.locator('#vault-form');
      assert.equal(await form.locator('[name=env]').inputValue(), 'GH_TOKEN', tag + ': the variable filled in');
      assert.equal(await form.locator('[name=name]').inputValue(), 'GitHub', tag + ': and the tool\'s name');
      assert.equal(await form.locator('[name=kind]').inputValue(), 'token');
      assert.equal(await sideways(page), false, tag + ': the dialog fits');
      await shot(page, 'phone-add-credential');
      await form.locator('[name=secret]').fill(SECRET);
      await form.locator('[type=submit]').click();
      await page.locator('.lc-creds .lc-cred .pill').filter({hasText: 'Set'}).waitFor();
      const created = seen.writes.filter(w => w.path === '/api/v2/credentials');
      assert.deepEqual(JSON.parse(created[0]!.body), {name: 'GitHub', username: '', kind: 'token', env: 'GH_TOKEN', source: 'github', secret: SECRET});
      await noSecret(page, tag);
      assert.equal(seen.writes.filter(w => w.body.includes(SECRET)).length, 1, tag + ': the value is sent once, to store it');
      assert.deepEqual(seen.errors, [], tag + ': no page errors');
      await page.close();
    }

    // A computer: a key no tool names has its own page, its access is granted from there and the list follows; a new
    // key is added from + on Tools.
    {
      const tag = 'desktop';
      const {page, seen} = await open(browser, {viewport: DESKTOP});
      await page.locator('tr[data-tl-row]').first().waitFor();
      const rail = await page.locator('.au-rail').boundingBox(), list = await page.locator('.au-c').boundingBox();
      assert.ok(rail!.x + rail!.width <= list!.x + 1, tag + ': rail | list');
      await shot(page, 'desktop-tools');
      await page.locator('tr[data-tl-row="credentials/c3"] .au-nm').click();
      assert.ok(await hashSoon(page, LIST + '/key/c3'), tag + ': the key\'s own address');
      await page.locator('#int-detail h1').filter({hasText: 'OpenAI key'}).waitFor();
      assert.equal(await page.locator('[data-tl-crumb]').innerText(), 'OpenAI key', tag + ': named in the breadcrumb');
      assert.match(await page.locator('#int-detail .lc-creds').innerText(), /OPENAI_API_KEY[\s\S]*Type\s+API key[\s\S]*Value\s+sk-/);
      assert.deepEqual(await page.locator('.lc-access .lc-wn').allInnerTexts(), ['Assistant'], tag + ': who it is granted to');
      await shot(page, 'desktop-credential');
      await page.locator('[data-vault-share="c3"]').click();
      await page.locator('#vault-grant-form select').selectOption('bot:eng-lead');
      await page.locator('#vault-grant-form [type=submit]').click();
      await page.locator('.lc-access .lc-wn').filter({hasText: 'Engineering Lead'}).waitFor();
      assert.deepEqual(seen.writes.map(w => [w.path, JSON.parse(w.body)]), [['/api/v2/credentials/c3/grants', {subject: 'bot:eng-lead'}]]);
      await page.locator('.tt-crumb a[href="#/tools"]').click();
      await page.locator('tr[data-tl-row="credentials/c3"]').waitFor();
      assert.equal(await page.locator('tr[data-tl-row="credentials/c3"] .tt-face').count(), 2, tag + ': the list follows');
      // + › Key: the vault's own Add.
      await page.locator('[data-tl-add]').click();
      assert.deepEqual(await page.locator('[data-tl-add-item]').evaluateAll(b => b.map(x => (x as HTMLElement).dataset.tlAddItem)).then(k => k.slice(-1)), ['key'], tag + ': Key last');
      await page.locator('[data-tl-add-item="key"]').click();
      await page.locator('#vault-form [name=name]').waitFor();
      assert.deepEqual(seen.errors, [], tag + ': no page errors');
      await page.close();
    }

    // A tool page that will not load says so; a key that is gone says so.
    {
      const tag = 'desktop missing';
      const {page, seen} = await open(browser, {viewport: DESKTOP, hash: LIST + '/aside'});
      await page.locator('#int-detail .empty').filter({hasText: 'No tool named'}).waitFor();
      await page.evaluate(h => { location.hash = h; }, LIST + '/key/nope');
      await page.locator('[data-lc-gone]').waitFor();
      assert.deepEqual(seen.errors, [], tag + ': no page errors');
      await page.close();
    }

    // The list is slow and the reader moves to a tool meanwhile: the tool's page stays, no errors.
    {
      const tag = 'desktop slow list';
      let release!: (value?: unknown) => void;
      const listGate = new Promise(r => { release = r; });
      const {page, seen} = await open(browser, {viewport: DESKTOP, hash: LIST, listGate});
      await page.evaluate(h => { location.hash = h; }, LIST + '/key/c3');
      await page.locator('#int-detail h1').filter({hasText: 'OpenAI key'}).waitFor();
      release();
      await page.waitForTimeout(300);
      assert.ok(await hashSoon(page, LIST + '/key/c3'), tag + ': the later address stays');
      assert.equal(await page.locator('#tools-root').count(), 0, tag + ': the list did not draw over it');
      assert.deepEqual(seen.errors, [], tag + ': no page errors');
      await page.close();
    }

    // The old addresses lead to Tools: Keys and its rows, Connections and the old Tools page to the tool's own page,
    // the old Credentials page to the keys.
    for (const [from, want] of [['#/library/connections/postgres', '#/tools/postgres'], ['#/integrations/postgres', '#/tools/postgres'],
                                ['#/credentials', LIST + '?show=keys'], ['#/tools/keys', LIST], ['#/tools/keys/close-crm', LIST + '/close-crm'],
                                ['#/tools/keys/credentials/c3', LIST + '/key/c3'], ['#/tools/keys?who=group:eng&issues=1', LIST + '?needs=1&who=group%3Aeng']] as const) {
      const {page, seen} = await open(browser, {viewport: DESKTOP, hash: from});
      assert.ok(await hashSoon(page, want), `${from} leads to ${want}`);
      assert.deepEqual(seen.errors, [], from + ': no page errors');
      await page.close();
    }
    // An old Keys link narrowed to a team and Issues opens the list that way.
    {
      const {page, seen} = await open(browser, {viewport: DESKTOP, hash: '#/tools/keys?who=group:eng&issues=1'});
      await page.locator('tr[data-tl-row]').first().waitFor();
      assert.deepEqual(await keys(page), ['service:close-crm'], 'old Keys filters: Engineering, with issues');
      assert.equal(await page.locator('[data-tl-who="group:eng"]').getAttribute('aria-pressed'), 'true');
      assert.equal(await page.locator('[data-tl-fneeds]').getAttribute('aria-pressed'), 'true');
      assert.deepEqual(seen.errors, [], 'old Keys filters: no page errors');
      await page.close();
    }

    // A member who cannot open the vault: the tools from what bots declare, no keys read, nothing to add to the vault.
    {
      const tag = 'member';
      const {page, seen} = await open(browser, {me: MEMBER, hash: LIST + '/close-crm'});
      await page.locator('#int-detail h1').filter({hasText: 'Close CRM'}).waitFor();
      assert.equal(seen.vaultReads, 0, tag + ': the vault is not read');
      assert.equal(await page.locator('[data-vault-edit], [data-lc-add-env]').count(), 0, tag + ': nothing to add');
      assert.equal(await page.locator('.lc-access .lc-who-row').count(), 2, tag + ': the bots that have it');
      await page.locator('.tt-crumb a[href="#/tools"]').click();
      await page.locator('tr[data-tl-row]').first().waitFor();
      assert.equal(await page.locator('tr[data-tl-row="credentials/c3"]').count(), 0, tag + ': no keys listed');
      await page.locator('[data-tl-add]').click();
      assert.equal(await page.locator('[data-tl-add-item="key"], [data-tl-add-item="api"]').count(), 0, tag + ': no key or API to add');
      assert.equal(seen.vaultReads, 0, tag + ': still not read');
      assert.deepEqual(seen.errors, [], tag + ': no page errors');
      await page.close();
    }

    // 200 bots in ten teams share a tool: one row, four faces and the rest counted; on its page each team folds, the
    // first open, or the team Tools was narrowed to.
    {
      const tag = 'desktop 200 bots';
      const slugs = Array.from({length: 200}, (_, i) => 'bot-' + String(i + 1).padStart(3, '0'));
      const emps = [...roster.employees, ...slugs.map(slug => ({name: slug, display_name: 'Bot ' + slug.slice(4), status: 'active', org_parent: ''}))];
      const groups = Array.from({length: 10}, (_, g) => ({id: 'team-' + g, name: 'Team ' + (g + 1), parent: '', people: [], bots: slugs.slice(g * 20, g * 20 + 20)}));
      const tools = FX.tools.integrations.map((t: Record<string, unknown>) => t.service !== 'close-crm' ? t : {...t, bots: slugs,
        connections: slugs.map((bot, i) => ({bot, status: i === 7 ? 'problem' : 'ready', identity: '', can: ['read'], write: 'never',
          ...(i === 7 ? {problem: 'Credential missing on Test Mac'} : {})}))});
      const {page, seen} = await open(browser, {viewport: DESKTOP, hash: LIST, tools, emps, groups});
      const row = page.locator('tr[data-tl-row="service:close-crm"]');
      await row.waitFor();
      assert.equal(await row.locator('.tt-face').count(), 4, tag + ': four faces');
      assert.match(await row.locator('.tt-more').innerText(), /\+198/, tag + ': 200 bots, the vault\'s grantee and Ana');
      await page.locator('[data-tl-who="group:team-3"]').click();
      assert.deepEqual(await keys(page), ['service:close-crm'], tag + ': a team narrows the list');
      await row.locator('.au-nm').click();
      await page.locator('.lc-access .lc-team').first().waitFor();
      assert.equal(await page.locator('.lc-access .lc-team').count(), 11, tag + ': ten teams, then No team');
      assert.deepEqual(await page.locator('.lc-access .lc-team[open] summary span:first-child').allInnerTexts(), ['Team 4'], tag + ': the team picked is the one open');
      assert.equal(await sideways(page), false, tag + ': no sideways scroll');
      await shot(page, 'desktop-tool-200-bots');
      await page.evaluate(h => { location.hash = h; }, LIST + '/postgres');
      await page.locator('#int-detail h1').filter({hasText: 'Acme Postgres'}).waitFor();
      await page.evaluate(h => { location.hash = h; }, LIST + '/close-crm');
      await page.locator('.lc-access .lc-team').first().waitFor();
      assert.equal(await page.locator('.lc-access .lc-team[open]').count(), 1, tag + ': folded, one open');
      assert.deepEqual(seen.errors, [], tag + ': no page errors');
      await page.close();
    }

    // A team with no tools: one line.
    {
      const tag = 'phone empty';
      const {page, seen} = await open(browser, {tools: [], me: MEMBER});
      await page.locator('[data-tl-empty]').filter({hasText: 'No tools yet.'}).waitFor();
      assert.equal(await page.locator('tr[data-tl-row], tr[data-tl-grp]').count(), 0, tag + ': no rows');
      assert.deepEqual(seen.errors, [], tag + ': no page errors');
      await page.close();
    }
    console.log('library tools ok');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
