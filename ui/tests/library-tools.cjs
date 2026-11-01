// Library › Tools (ui/app/redesign-library.js) against fixtures/redesign-tools.json (the Acme example, shaped as GET
// /api/v2/tools, /api/v2/tools/{service}, /api/v2/credentials and /api/v2/groups answer): one row per tool with its
// kind, a red dot when it has an issue and how many teammates have it, grouped In use / Available / Credentials /
// Removed; search, who has access (a bot, a person, or a team with the teams under it) and Issues narrow the list and
// ride in the address. A row opens the tool: its issues, who has it (each with what they may change there), its
// credentials (set or not, when a bot last used it, Rotate / Access / Add), then the page, its queries and learnings.
// A credential no tool names has its own page. The old addresses (#/library/connections, #/integrations, #/credentials)
// lead here. A member who cannot open the vault never reads it. No secret value is ever on the page.
// Phone first (390×844), then a computer. TICO_UI_SHOTS=<dir> saves screenshots.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const uiDir = path.join(__dirname, '..');
const fixture = name => JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', name), 'utf8'));
const FX = fixture('redesign-tools.json');
const roster = fixture('redesign-roster.json');
const PHONE = {width: 390, height: 844}, DESKTOP = {width: 1440, height: 900};
const LIST = '#/library/tools';
const SECRET = 'ghp-secret-value-5c4b3a';
const OWNER = {id: 'ana', name: 'Ana Silva', email: 'ana@acme.example', role: 'owner', company_role: 'owner', cloud: true, credential_access: true, bot_admin: true};
const MEMBER = {id: 'cara', name: 'Cara Diaz', email: 'cara@acme.example', role: 'human', company_role: 'member', cloud: true, credential_access: false};

async function open(browser, {viewport = PHONE, hash = LIST, me = OWNER, tools = FX.tools.integrations, listGate = null, emps = roster.employees, groups = FX.groups} = {}) {
  const page = await browser.newPage({viewport, serviceWorkers: 'block', hasTouch: viewport === PHONE});
  const seen = {errors: [], writes: [], vaultReads: 0, vault: JSON.parse(JSON.stringify(FX.vault))};
  page.on('pageerror', e => seen.errors.push(e.message));
  const config = {company_name: 'Acme'};
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname, method = req.method();
    const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (url.origin !== 'http://tico-ui.test') return route.abort();
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const file = p.startsWith('/tico/ui/') ? uiFile(p.slice(9)) : p.startsWith('/vendor/') ? path.join(uiDir, p) : '';
    if (file && fs.existsSync(file)) return route.fulfill({contentType: file.endsWith('.js') ? 'application/javascript' : file.endsWith('.css') ? 'text/css' : file.endsWith('.woff2') ? 'font/woff2' : 'application/octet-stream', body: fs.readFileSync(file)});
    if (p === '/api/v2/events' || p.endsWith('/watch')) return;
    if (method !== 'GET') seen.writes.push({method, path: p, body: req.postData() || ''});
    if (p === '/api/v2/config') return json(config);
    if (p === '/api/me') return json({...me, config});
    if (p === '/api/employees') return json(emps);
    if (p === '/api/humans') return json(roster.humans);
    if (p === '/api/status') return json({active: [], employees: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/groups') return json(groups);
    const m = p.match(/^\/api\/v2\/(?:tools|integrations)(?:\/([^/]+))?$/);
    if (m && !m[1]) { await listGate; return json({label: 'Available tools', integrations: tools}); }
    if (m) {
      const one = FX.pages[decodeURIComponent(m[1])];
      return one ? json(one) : json({error: {code: 'not_found', detail: 'No tool named ' + m[1]}}, 404);
    }
    if (p === '/api/v2/credentials' && method === 'GET') {
      if (!me.credential_access) return json({error: {code: 'forbidden', detail: 'No'}}, 403);
      seen.vaultReads++;
      return json(seen.vault);
    }
    if (p === '/api/v2/credentials' && method === 'POST') {
      const body = JSON.parse(req.postData());
      seen.vault.credentials.push({id: 'c9', name: body.name, username: body.username, kind: body.kind, env: body.env, preview: 'ghp-••••3a',
        source: body.source, revision: 1, created_at: '2026-10-28T09:00:00Z', updated_at: '2026-10-28T09:00:00Z', stored: true, can_reveal: true,
        last_used_at: null, last_used_by: '', grants: []});
      return json({id: 'c9', name: body.name});
    }
    const grant = p.match(/^\/api\/v2\/credentials\/([^/]+)\/grants$/);
    if (grant) {
      const cred = seen.vault.credentials.find(c => c.id === grant[1]);
      cred.grants.push({id: 'g9', subject: JSON.parse(req.postData()).subject, granted_by: 'human:ana', parent_id: null, can_revoke: true});
      return json({id: 'g9'});
    }
    return json(unrouted(p));
  });
  await page.goto('http://tico-ui.test/' + hash);
  await page.waitForFunction(() => typeof S !== 'undefined' && S.me && (S.emps || []).length);
  return {page, seen};
}
const sideways = page => page.evaluate(() => [document.documentElement, document.querySelector('#main')].some(el => el && el.scrollWidth > el.clientWidth + 1));
const hashSoon = (page, want) => page.waitForFunction(w => location.hash === w, want).then(() => true, () => false);
async function shot(page, name) {
  const dir = process.env.TICO_UI_SHOTS;
  if (!dir) return;
  fs.mkdirSync(dir, {recursive: true});
  await page.screenshot({animations: 'disabled', path: path.join(dir, name + '.png')});
}
// [key, icon, name · kind, issue dot, how many have it]
const rows = page => page.locator('.lc-row').evaluateAll(els => els.map(el => [el.dataset.key,
  el.querySelector('.nav-icon').textContent, el.querySelector('.lc-nm').innerText.trim(), !!el.querySelector('.lc-dot'), el.querySelector('.lc-n').innerText.trim()]));
const keys = page => page.locator('.lc-row').evaluateAll(els => els.map(el => el.dataset.key));
const ROWS = [
  ['close-crm', 'api', 'Close CRM · API', true, '3'],
  ['postgres', 'inventory_2', 'Acme Postgres · SQL', false, '1'],
  ['acme-ops', 'link', 'Acme Ops MCP · MCP', false, '1'],
  ['aside', 'travel_explore', 'Aside browser · Browser', false, '0'],
  ['mail', 'mail', 'Mail · Email', true, '0'],
  ['github', 'code', 'GitHub · CLI', false, '0'],
  ['credentials/c3', 'key_vertical', 'OpenAI key · API key', false, '1'],
  ['old-wiki', 'api', 'old-wiki · API', false, '0'],
];
const noSecret = async (page, tag) => assert.ok(!(await page.content()).includes(SECRET), tag + ': no secret value on the page');

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    // A phone: the list, the filters, a tool, its access and credentials, back.
    {
      const tag = 'phone';
      const {page, seen} = await open(browser);
      await page.locator('.lc-row').first().waitFor();
      assert.deepEqual(await rows(page), ROWS, tag + ': one row each: kind, issue dot, how many have it');
      assert.deepEqual(await page.locator('.lc-gh').allInnerTexts(), ['IN USE\n3', 'AVAILABLE\n3', 'CREDENTIALS\n1', 'REMOVED\n1'], tag + ': grouped');
      assert.equal(await page.locator('#side a[data-nav="tools"].cur').count(), 1, tag + ': Tools is current');
      assert.equal(await page.locator('#side a[data-nav="tools"]').innerText(), 'Tools', tag + ': the nav says Tools');
      assert.equal(await page.locator('#account-menu [href="#/integrations"]').count(), 0, tag + ': no second Tools in the account menu');
      assert.equal(await page.locator('.rd-help').count(), 1, tag + ': one help bubble');
      assert.equal(await page.locator('.lc-pane').isVisible(), false, tag + ': the list alone');
      assert.equal(await sideways(page), false, tag + ': no sideways scroll');
      await shot(page, 'phone-tools');

      // Who has access: the picker lists teams (nested ones indented), people and bots.
      const options = await page.locator('.lc-who optgroup').evaluateAll(gs => gs.map(g => [g.label, [...g.children].map(o => o.textContent.trim())]));
      assert.deepEqual(options.map(([label]) => label), ['Teams', 'People', 'Bots'], tag + ': teams, people, bots');
      assert.deepEqual(options[0][1], ['Engineering', 'Platform', 'Operations'], tag + ': the teams in order');
      await page.locator('.lc-who').selectOption('group:eng');
      assert.deepEqual(await keys(page), ['close-crm', 'postgres', 'acme-ops'], tag + ': a team counts the teams under it');
      assert.ok(await hashSoon(page, LIST + '?who=group%3Aeng'), tag + ': the filter is in the address');
      await page.locator('.lc-who').selectOption('bot:builder');
      assert.deepEqual(await keys(page), ['postgres'], tag + ': one bot');
      await page.locator('.lc-who').selectOption('human:ana');
      assert.deepEqual(await keys(page), ['close-crm'], tag + ': a person, by a credential granted to her');
      await page.locator('.lc-who').selectOption('group:ops');
      assert.deepEqual(await keys(page), ['close-crm', 'credentials/c3'], tag + ': a team, its credentials too');
      await page.locator('.lc-who').selectOption('');
      assert.equal(await page.locator('.lc-in').innerText(), '2', tag + ': two tools have issues');
      await page.locator('.lc-issues').click();
      assert.deepEqual(await keys(page), ['close-crm', 'mail'], tag + ': Issues only');
      await page.locator('.lc-q').fill('mail');
      assert.deepEqual(await keys(page), ['mail'], tag + ': search within them');
      await page.locator('.lc-q').fill('nothing-like-it');
      await page.locator('.lc-scroll .lc-pad').filter({hasText: 'Nothing matches.'}).waitFor();
      await page.locator('.lc-q').fill('');
      await page.locator('.lc-issues').click();
      assert.equal(await page.locator('.lc-row').count(), ROWS.length, tag + ': all again');

      await page.locator('.lc-row[data-key="close-crm"]').click();
      assert.ok(await hashSoon(page, LIST + '/close-crm'), tag + ': its own address');
      await page.locator('#int-detail h1').filter({hasText: 'Close CRM'}).waitFor();
      assert.equal(await page.locator('.lc-list').isVisible(), false, tag + ': the page alone');
      // In order: issues, access, credentials, then the page.
      const order = await page.locator('#int-detail .card').evaluateAll(cs => cs.map(c => c.className.split(' ').find(x => x.startsWith('lc-')) || c.id || 'meta'));
      assert.deepEqual(order.slice(0, 4), ['lc-issues-card', 'lc-access', 'lc-creds', 'meta'], tag + ': issues, access, credentials, then the page');
      assert.match(await page.locator('.lc-issues-card').innerText(), /Credential missing on Test Mac · Release Manager/);
      const access = await page.locator('.lc-access .lc-team').evaluateAll(ts => ts.map(t => [t.querySelector('summary span').textContent,
        [...t.querySelectorAll('.lc-who-row')].map(r => [r.querySelector('.lc-wn').textContent, r.querySelector('.pill')?.textContent || ''])]));
      assert.deepEqual(access, [
        ['Engineering', [['Engineering Lead', 'Approval']]],
        ['Operations', [['Ana Silva', 'Approval'], ['Release Manager', 'Read only']]],
      ], tag + ': who has it, by team, each with what they may change');
      assert.equal(await page.locator('.lc-access .lc-who-row .lc-dot').count(), 1, tag + ': the bot with the problem is marked');
      const cred = await page.locator('.lc-creds .lc-cred').innerText();
      assert.match(cred, /Close CRM/); assert.match(cred, /CLOSE_API_KEY/); assert.match(cred, /Set/); assert.match(cred, /used .* by Engineering Lead/);
      assert.deepEqual(await page.locator('.lc-creds .lc-acts button').allInnerTexts(), ['Rotate', 'Access', 'Reveal'], tag + ': the vault\'s actions');
      assert.equal(await page.locator('[data-learning-count]').innerText(), '2', tag + ': the learnings counted');
      assert.equal(await page.locator('#int-learn-add').count(), 1, tag + ': a learning can be added');
      assert.equal(await page.getAttribute('#int-body a[href^="#/"]', 'href'), LIST + '/postgres', tag + ': a sibling page stays in Library');
      assert.equal(await sideways(page), false, tag + ': the page fits');
      await shot(page, 'phone-tool');

      await page.locator('.lc-back').click();
      assert.ok(await hashSoon(page, LIST), tag + ': back to the list');
      await page.locator('.lc-list').waitFor();
      await noSecret(page, tag);
      assert.deepEqual(seen.writes, [], tag + ': nothing written');
      assert.deepEqual(seen.errors, [], tag + ': no page errors');
      await page.close();
    }

    // A phone: a tool whose credential has no value, and one whose variable the vault does not hold: Add, prefilled.
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
      assert.deepEqual(JSON.parse(created[0].body), {name: 'GitHub', username: '', kind: 'token', env: 'GH_TOKEN', source: 'github', secret: SECRET});
      await noSecret(page, tag);
      assert.equal(seen.writes.filter(w => w.body.includes(SECRET)).length, 1, tag + ': the value is sent once, to store it');
      assert.deepEqual(seen.errors, [], tag + ': no page errors');
      await page.close();
    }

    // A computer: the first row opens beside the list; a credential no tool names has its own page, and its access is
    // granted from there; the filters stay in each row's address.
    {
      const tag = 'desktop';
      const {page, seen} = await open(browser, {viewport: DESKTOP});
      assert.ok(await hashSoon(page, LIST + '/close-crm'), tag + ': opens on the first row');
      await page.locator('#int-detail h1').filter({hasText: 'Close CRM'}).waitFor();
      await page.locator('.lc-access .lc-who-row').first().waitFor();
      assert.equal(await page.locator('.lc-back').isVisible(), false, tag + ': no back link beside the list');
      assert.equal(await page.locator('.lc-row.sel').getAttribute('data-key'), 'close-crm', tag + ': the row is selected');
      const list = await page.locator('.lc-list').boundingBox(), pane = await page.locator('.lc-pane').boundingBox();
      assert.ok(list.x + list.width <= pane.x + 1, tag + ': list | page');
      assert.equal(await page.locator('.lc-add button').innerText(), 'Add credential');
      await shot(page, 'desktop-tool');

      await page.locator('.lc-issues').click();
      assert.equal(await page.getAttribute('.lc-row[data-key="mail"]', 'href'), LIST + '/mail?issues=1', tag + ': rows keep the filter');
      await page.locator('.lc-issues').click();
      await page.locator('.lc-row[data-key="credentials/c3"]').click();
      await page.locator('#int-detail h1').filter({hasText: 'OpenAI key'}).waitFor();
      assert.match(await page.locator('#int-detail .lc-creds').innerText(), /OPENAI_API_KEY[\s\S]*Type\s+API key[\s\S]*Value\s+sk-/);
      assert.deepEqual(await page.locator('.lc-access .lc-wn').allInnerTexts(), ['Assistant'], tag + ': who it is granted to');
      await shot(page, 'desktop-credential');
      await page.locator('[data-vault-share="c3"]').click();
      await page.locator('#vault-grant-form select').selectOption('bot:eng-lead');
      await page.locator('#vault-grant-form [type=submit]').click();
      await page.locator('.lc-access .lc-wn').filter({hasText: 'Engineering Lead'}).waitFor();
      assert.equal(await page.locator('.lc-row[data-key="credentials/c3"] .lc-n').innerText(), '2', tag + ': the list follows');
      assert.deepEqual(seen.writes.map(w => [w.path, JSON.parse(w.body)]), [['/api/v2/credentials/c3/grants', {subject: 'bot:eng-lead'}]]);

      await page.locator('.lc-row[data-key="aside"]').click();
      await page.locator('#int-detail .empty').filter({hasText: 'No tool named'}).waitFor();
      assert.ok(await page.locator('.lc-list').isVisible(), tag + ': a page that will not load leaves the list');
      assert.deepEqual(seen.errors, [], tag + ': no page errors');
      await page.close();
    }

    // The list is slow and the address moves to another tool meanwhile: the later address wins, no errors.
    {
      const tag = 'desktop slow list';
      let release;
      const listGate = new Promise(r => { release = r; });
      const {page, seen} = await open(browser, {viewport: DESKTOP, hash: LIST + '/close-crm', listGate});
      await page.evaluate(h => { location.hash = h; }, LIST + '/postgres');
      await page.waitForFunction(() => location.hash.endsWith('/postgres'));
      release();
      await page.locator('#int-detail h1').filter({hasText: 'Acme Postgres'}).waitFor();
      assert.equal(await page.locator('.lc-row.sel').getAttribute('data-key'), 'postgres', tag + ': the later address is selected');
      assert.ok(await hashSoon(page, LIST + '/postgres'), tag + ': and stays');
      assert.deepEqual(seen.errors, [], tag + ': no page errors');
      await page.close();
    }

    // The old addresses lead here: Connections, the old Tools page (with its tool), the old Credentials page.
    for (const [from, want] of [['#/library/connections/postgres', LIST + '/postgres'], ['#/integrations/postgres', LIST + '/postgres'],
                                ['#/credentials', LIST + '/credentials/c3']]) {
      const {page, seen} = await open(browser, {viewport: DESKTOP, hash: from});
      assert.ok(await hashSoon(page, want), `${from} leads to ${want}`);
      assert.deepEqual(seen.errors, [], from + ': no page errors');
      await page.close();
    }

    // A member who cannot open the vault: the list from what bots declare, no credentials read, nothing to add.
    {
      const tag = 'member';
      const {page, seen} = await open(browser, {me: MEMBER, hash: LIST + '/close-crm'});
      await page.locator('#int-detail h1').filter({hasText: 'Close CRM'}).waitFor();
      assert.equal(seen.vaultReads, 0, tag + ': the vault is not read');
      assert.equal(await page.locator('.lc-add button, [data-vault-edit], [data-lc-add-env]').count(), 0, tag + ': nothing to add');
      assert.equal(await page.locator('.lc-access .lc-who-row').count(), 2, tag + ': the bots that have it');
      await page.locator('.lc-back').click();
      assert.equal(await page.locator('.lc-gh[data-group="Credentials"]').count(), 0, tag + ': no credentials group');
      assert.deepEqual(seen.errors, [], tag + ': no page errors');
      await page.close();
    }

    // 200 bots in ten teams share a tool: one row, the count; on its page each team folds, the first open.
    {
      const tag = 'desktop 200 bots';
      const slugs = Array.from({length: 200}, (_, i) => 'bot-' + String(i + 1).padStart(3, '0'));
      const emps = [...roster.employees, ...slugs.map(slug => ({name: slug, display_name: 'Bot ' + slug.slice(4), status: 'active', org_parent: ''}))];
      const groups = Array.from({length: 10}, (_, g) => ({id: 'team-' + g, name: 'Team ' + (g + 1), parent: '', people: [], bots: slugs.slice(g * 20, g * 20 + 20)}));
      const tools = FX.tools.integrations.map(t => t.service !== 'close-crm' ? t : {...t, bots: slugs,
        connections: slugs.map((bot, i) => ({bot, status: i === 7 ? 'problem' : 'ready', identity: '', can: ['read'], write: 'never',
          ...(i === 7 ? {problem: 'Credential missing on Test Mac'} : {})}))});
      const {page, seen} = await open(browser, {viewport: DESKTOP, hash: LIST + '/close-crm', tools, emps, groups});
      await page.locator('.lc-access .lc-team').first().waitFor();
      assert.equal(await page.locator('.lc-row[data-key="close-crm"] .lc-n').innerText(), '202', tag + ': 200 bots, the vault\'s grantee and Ana');
      assert.equal(await page.locator('.lc-access .lc-team').count(), 11, tag + ': ten teams, then No team');
      assert.equal(await page.locator('.lc-access .lc-team[open]').count(), 1, tag + ': folded, the first open');
      assert.equal(await page.locator('.lc-who optgroup[label="Bots"] option').count(), emps.length, tag + ': every bot in the picker');
      await page.locator('.lc-who').selectOption('group:team-3');
      assert.deepEqual(await keys(page), ['close-crm'], tag + ': a team narrows the list');
      assert.deepEqual(await page.locator('.lc-access .lc-team[open] summary span:first-child').allInnerTexts(), ['Team 4'], tag + ': the team picked is the one open');
      assert.equal(await sideways(page), false, tag + ': no sideways scroll');
      await shot(page, 'desktop-tool-200-bots');
      assert.deepEqual(seen.errors, [], tag + ': no page errors');
      await page.close();
    }

    // A team with no tools: one line.
    {
      const tag = 'phone empty';
      const {page, seen} = await open(browser, {tools: [], me: MEMBER});
      await page.locator('.lc-scroll .lc-pad').filter({hasText: 'No tools yet.'}).waitFor();
      assert.equal(await page.locator('.lc-row, .lc-gh').count(), 0, tag + ': no rows');
      assert.deepEqual(seen.errors, [], tag + ': no page errors');
      await page.close();
    }
    console.log('library tools ok');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
