// Credentials in Library › Tools: an owner (or credential admin) adds a credential from the page's header, finds it on
// the tool whose variable it fills, and grants it to a bot. The secret is sent once, in the create request, and is
// never shown again (only its preview); a member with no vault access never reads the vault and has nothing to add.
// The old Credentials address lands here. A website login shows its username, sites and a 2FA mark, Show code reads
// the current code (never the key), Add takes a login's password, 2FA key and sites, and Import sends a password
// manager's export once. Fixtures only, no network.
const {chromium, webkit} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');

const SECRET = 'jira-token-value-9f8e7d6c5b4a';
const PASSWORD = 'acme portal 55!', TOTP = 'jbsw y3dp ehpk 3pxp';
const SHOTS = process.env.TICO_UI_SHOTS || '';
const shot = async (page, name) => { if (SHOTS) { await page.waitForTimeout(400); await page.screenshot({path: require('node:path').join(SHOTS, name)}); } };

async function open(browser, me, vault, viewport = {width: 1200, height: 900}) {
  const page = await browser.newPage({viewport, serviceWorkers: 'block', hasTouch: viewport.width < 760});
  const seen = {errors: [], posts: [], vault: JSON.parse(JSON.stringify(vault)), reads: 0, codes: 0};
  page.on('pageerror', e => seen.errors.push(e.message));
  await page.route('**/*', route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname;
    const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1]))) return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
    if (/\/marked\.min\.js$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: 'window.marked={parse:s=>String(s)}'});
    if (url.origin !== 'https://tico-ui.test') return route.abort();
    if (req.method() !== 'GET') seen.posts.push({path: p, body: req.postData() || ''});
    if (p === '/api/me') return json({...me, config: {}});
    if (p === '/api/v2/config') return json({});
    if (p === '/api/employees') return json([{name: 'jira-manager', display_name: 'Jira Manager', status: 'active'}]);
    if (p === '/api/issues') return json([]);
    const jira = {service: 'jira', title: 'Jira', kind: 'api', summary: 'Tickets', access: 'read', credentials: ['JIRA_BASIC_AUTH'], writes: 'never',
      declared_as: '', owner: 'Ana', aliases: [], status: 'not_configured', bots: [], connections: []};
    if (p === '/api/v2/tools') return json({integrations: [jira]});
    if (p === '/api/v2/tools/jira') return json({...jira, queries: [], body: 'Tickets.', learnings: []});
    if (p === '/api/v2/groups') return json([]);
    if (p === '/api/v2/credentials' && req.method() === 'GET') { seen.reads++; return json(seen.vault); }
    if (p === '/api/v2/credentials' && req.method() === 'POST') {
      const body = JSON.parse(req.postData());
      seen.vault.credentials.push({id: 'c1', name: body.name, username: body.username, kind: body.kind, env: body.env || '', preview: '••••••', source: '', revision: 1,
        stored: true, can_reveal: true, last_used: null, last_used_by: '', grants: [], ...(body.kind === 'login' ? {sites: body.sites, has_totp: !!body.totp} : {})});
      return json({id: 'c1', name: body.name});
    }
    if (p === '/api/v2/credentials/l1/code') { seen.codes++; return json({code: '492039', expires_in: 17}); }
    if (p === '/api/v2/credentials/import') return json({imported: 2, skipped: 1, errors: []});
    if (p === '/api/v2/credentials/c1/grants') {
      seen.vault.credentials[0].grants.push({id: 'g1', subject: JSON.parse(req.postData()).subject, can_revoke: true});
      return json({id: 'g1'});
    }
    if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
  return {page, seen};
}

(async () => {
  const browser = process.env.TICO_BROWSER === 'webkit' ? await webkit.launch({headless: true})
    : await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined, headless: true});
  try {
    const vault = {credentials: [], can_manage: true, configured: true,
      people: [{id: 'ana', name: 'Ana', email: 'ana@acme.example'}], bots: [{id: 'jira-manager', name: 'Jira Manager', operator: 'ana'}]};
    {
      const {page, seen} = await open(browser, {id: 'ana', name: 'Ana', role: 'owner', cloud: true, credential_access: true}, vault);
      await page.goto('https://tico-ui.test/#/credentials');
      await page.waitForFunction(() => location.hash === '#/library/tools/jira');
      await page.locator('#int-detail h1').filter({hasText: 'Jira'}).waitFor();
      // The tool names a variable the vault does not hold yet.
      await page.locator('.lc-creds [data-lc-add-env="JIRA_BASIC_AUTH"]').waitFor();

      await page.locator('.lc-add [data-lc-add]').click();
      const form = page.locator('#vault-form');
      await form.locator('[name=name]').fill('Jira login');
      await form.locator('[name=env]').fill('JIRA_BASIC_AUTH');
      await form.locator('[name=secret]').fill(SECRET);
      await form.locator('[type=submit]').click();
      await page.locator('.lc-creds [data-vault-share]').waitFor();
      const created = seen.posts.filter(r => r.path === '/api/v2/credentials');
      assert.equal(created.length, 1);
      assert.deepEqual(JSON.parse(created[0].body), {name: 'Jira login', username: '', kind: 'api_key', env: 'JIRA_BASIC_AUTH', source: '', secret: SECRET});

      // Never shown again: the tool's credential has the name, the variable and whether it is set.
      const row = await page.locator('.lc-creds').innerText();
      assert.match(row, /Jira login/);
      assert.match(row, /JIRA_BASIC_AUTH/);
      assert.match(row, /Set/);
      assert.ok(!(await page.content()).includes(SECRET), 'the secret is not on the page');

      // Grant it to a bot: the tool's access follows.
      await page.locator('.lc-creds [data-vault-share]').click();
      await page.locator('#vault-grant-form select').selectOption('bot:jira-manager');
      await page.locator('#vault-grant-form [type=submit]').click();
      await page.locator('.lc-access .lc-wn').filter({hasText: 'Jira Manager'}).waitFor();
      assert.equal(await page.locator('.lc-row[data-key="jira"] .lc-n').innerText(), '1');
      const grants = seen.posts.filter(r => r.path === '/api/v2/credentials/c1/grants');
      assert.deepEqual(grants.map(r => JSON.parse(r.body)), [{subject: 'bot:jira-manager'}]);
      assert.ok(seen.posts.filter(r => r.body.includes(SECRET)).every(r => r.path === '/api/v2/credentials'), 'the secret is sent only when it is created');
      assert.deepEqual(seen.errors, []);
      await page.close();
    }
    {
      // No vault access: nothing to add, and the vault is not even read.
      const {page, seen} = await open(browser, {id: 'cara', name: 'Cara', role: 'human', cloud: true, credential_access: false}, vault);
      await page.goto('https://tico-ui.test/#/integrations');
      await page.locator('#int-detail h1').filter({hasText: 'Jira'}).waitFor();
      assert.equal(await page.locator('.lc-add button, [data-lc-add-env], [data-vault-share]').count(), 0);
      assert.equal(seen.reads, 0, 'the vault is not even read');
      assert.deepEqual(seen.errors, []);
      await page.close();
    }
    // Website logins, at a computer's width and a phone's.
    for (const [tag, viewport] of [['desktop', {width: 1200, height: 900}], ['phone', {width: 390, height: 844}]]) {
      const login = {id: 'l1', name: 'Acme portal', username: 'ops@acme.example', kind: 'login', env: '', sites: ['portal.acme.example', 'sso.acme.example'],
        has_totp: true, can_code: true, preview: '', source: 'import', revision: 3, stored: true, can_reveal: true, last_used: null, last_used_by: '', grants: []};
      const {page, seen} = await open(browser, {id: 'ana', name: 'Ana', role: 'owner', cloud: true, credential_access: true}, {...vault, credentials: [login]}, viewport);
      await page.goto('https://tico-ui.test/#/library/tools/credentials/l1');
      await page.locator('#int-detail h1').filter({hasText: 'Acme portal'}).waitFor();
      // The list row and the page: username, sites, 2FA; never a value preview.
      if (tag === 'desktop') {
        const row = page.locator('.lc-row[data-key="credentials/l1"]');
        assert.match(await row.innerText(), /ops@acme\.example · portal\.acme\.example \+1/);
        assert.equal(await row.locator('.lc-2fa').innerText(), '2FA');
      }
      const facts = await page.locator('#tool-facts .lc-creds').innerText();
      assert.match(facts, /portal\.acme\.example, sso\.acme\.example/);
      assert.match(facts, /2FA\s+On/);
      assert.doesNotMatch(facts, /Value/);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, tag + ': no sideways scroll');
      await shot(page, `credentials-login-${tag}.png`);

      // Show code: six digits and the seconds left, read from the code route.
      await page.locator('[data-vault-code="l1"]').click();
      await page.locator('#credential-dialog [data-code]').filter({hasText: '492 039'}).waitFor();
      assert.match(await page.locator('#credential-dialog [data-left]').innerText(), /^1[67]s$/);
      assert.equal(seen.codes, 1);
      await shot(page, `credentials-code-${tag}.png`);
      await page.locator('[data-vault-close]').click();

      // Add a login: its sites as hostnames, the password and 2FA key sent once, in the create request.
      if (tag === 'phone') { await page.locator('.lc-back').click(); await page.locator('.lc-add [data-lc-add]').waitFor(); }
      await page.locator('.lc-add [data-lc-add]').click();
      const form = page.locator('#vault-form');
      await form.locator('[name=kind]').selectOption('login');
      assert.equal(await form.locator('[name=env]').isVisible(), false, 'a login has no bot variable');
      assert.equal(await form.locator('[name=secret]').isVisible(), false);
      await form.locator('[name=username]').fill('ana');
      await form.locator('[name=sites]').fill('https://App.Acme.example/login, crm.acme.example:8443');
      await form.locator('[name=password]').fill(PASSWORD);
      await form.locator('[name=totp]').fill(TOTP);
      assert.equal(await form.locator('label').first().innerText().then(t => t.trim().split('\n')[0]), 'Type', 'Type comes first');
      await shot(page, `credentials-add-login-${tag}-v2.png`);
      await form.locator('[type=submit]').click();
      await page.locator('#credential-dialog[open]').waitFor({state: 'detached'}).catch(() => {});
      await page.waitForFunction(() => !document.querySelector('#credential-dialog')?.open);
      const created = seen.posts.filter(r => r.path === '/api/v2/credentials');
      assert.deepEqual(JSON.parse(created[0].body), {name: 'app.acme.example', username: 'ana', kind: 'login', sites: ['app.acme.example', 'crm.acme.example'],
        source: '', password: PASSWORD, totp: TOTP});

      // Import: a Bitwarden export goes once to the import route; the counts are said.
      const content = JSON.stringify({items: [{type: 1, name: 'Acme', login: {username: 'a', password: PASSWORD}}]});
      await page.locator('.lc-add [data-lc-import-file]').setInputFiles({name: 'bitwarden_export.json', mimeType: 'application/json', buffer: Buffer.from(content)});
      await page.getByText('2 imported, 1 skipped').waitFor();
      const imports = seen.posts.filter(r => r.path === '/api/v2/credentials/import');
      assert.deepEqual(imports.map(r => JSON.parse(r.body)), [{format: 'bitwarden', content}]);
      assert.ok(seen.posts.filter(r => r.body.includes(PASSWORD.slice(0, 11))).every(r => ['/api/v2/credentials', '/api/v2/credentials/import'].includes(r.path)));
      assert.ok(!(await page.content()).includes(PASSWORD), 'the password is not on the page');
      if (tag === 'phone') {
        const head = await page.locator('.lc-ph').boundingBox();
        assert.ok(head.x + head.width <= viewport.width + 1, 'phone: the header fits');
      }
      await shot(page, `credentials-list-${tag}.png`);
      assert.deepEqual(seen.errors, []);
      await page.close();
    }
    console.log('credentials: ok');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
