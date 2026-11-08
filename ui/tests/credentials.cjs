// Credentials in Library › Tools: an owner (or credential admin) adds a credential from the page's header, finds it on
// the tool whose variable it fills, and grants it to a bot. The secret is sent once, in the create request, and is
// never shown again (only its preview); a member with no vault access never reads the vault and has nothing to add.
// The old Credentials address lands here. Fixtures only, no network.
const {chromium, webkit} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');

const SECRET = 'jira-token-value-9f8e7d6c5b4a';

async function open(browser, me, vault) {
  const page = await browser.newPage({viewport: {width: 1200, height: 900}, serviceWorkers: 'block'});
  const seen = {errors: [], posts: [], vault: JSON.parse(JSON.stringify(vault)), reads: 0};
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
      declared_as: '', owner_id: 'Ana', aliases: [], status: 'not_configured', bots: [], connections: []};
    if (p === '/api/v2/tools') return json({integrations: [jira]});
    if (p === '/api/v2/tools/jira') return json({...jira, queries: [], body: 'Tickets.', learnings: []});
    if (p === '/api/v2/groups') return json([]);
    if (p === '/api/v2/credentials' && req.method() === 'GET') { seen.reads++; return json(seen.vault); }
    if (p === '/api/v2/credentials' && req.method() === 'POST') {
      const body = JSON.parse(req.postData());
      seen.vault.credentials.push({id: 'c1', name: body.name, username: body.username, kind: body.kind, env: body.env, preview: '••••••', source: '', revision: 1,
        stored: true, can_reveal: true, last_used_at: null, last_used_by: '', grants: []});
      return json({id: 'c1', name: body.name});
    }
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
      people: [{id: 'ana', name: 'Ana', email: 'ana@acme.example'}], bots: [{id: 'jira-manager', name: 'Jira Manager', owner_id: 'ana'}]};
    {
      const {page, seen} = await open(browser, {id: 'ana', name: 'Ana', role: 'owner', cloud: true, credential_access: true}, vault);
      await page.goto('https://tico-ui.test/#/credentials');
      await page.waitForFunction(() => location.hash === '#/library/tools/jira');
      await page.locator('#int-detail h1').filter({hasText: 'Jira'}).waitFor();
      // The tool names a variable the vault does not hold yet.
      await page.locator('.lc-creds [data-lc-add-env="JIRA_BASIC_AUTH"]').waitFor();

      await page.locator('.lc-add button').click();
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
    console.log('credentials: ok');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
