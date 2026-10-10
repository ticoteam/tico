// Offline browser regression: a Settings or Tools form keeps what a person has typed while the app's
// polls run. Types into Tools > GitHub, and into Settings > Computers (Add computer, API token), fires the
// refresh paths and asserts the values survive.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 900}, serviceWorkers: 'block'});
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    const writes = [];
    const computers = [{id: 'computer-1', label: 'Build server', operator: 'ana', bots: ['helper'], platform: 'linux',
      last_seen: new Date().toISOString(), readiness: {runtimes: {codex: {installed: true, authenticated: 'missing'}}}}];
    let providers = {enabled: ['openai'], default: {runtime: 'codex', model: 'test-model'}, revision: 1,
      providers: [{id: 'openai', label: 'OpenAI', recommended: 'test-model'}]};
    const bots = [{name: 'helper', display_name: 'Helper', status: 'active', operator: 'ana', can_manage: true, users: [], bot_owners: []}];
    await page.route('**/*', route => {
      const req = route.request(), p = new URL(req.url()).pathname;
      const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
      if (ui && fs.existsSync(uiFile(ui[1])))
        return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
      if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      const config = {version: '0.1.0', app_name: 'Tico', local: true, compose_project: 'acme', server_network: 'acme_default'};
      if (p === '/api/me') return json({id: 'ana', role: 'owner', name: 'Ana', email: 'ana@example.com', cloud: true, registered: true, config});
      if (p === '/api/v2/config') return json(config);
      if (p === '/api/employees') return json(bots);
      if (p === '/api/issues') return json([]);
      if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana'}], teams: {}});
      if (p === '/api/people') return json({people: [], teams: {}});
      if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: []});
      if (p === '/api/v2/status') return json({bots: []});
      if (p === '/api/v2/needs-you') return json({items: []});
      if (p === '/api/v2/integrations') return json({integrations: []});
      if (p === '/api/v2/github/app') return json({connected: false});
      if (p === '/api/v2/operations') return json({machines: computers, services: []});
      if (p === '/api/v2/models') return json({models: [{id: 'test-model', label: 'Test model', provider: 'openai', runtime: 'codex'}]});
      if (p === '/api/v2/providers') {
        if (req.method() === 'PUT') { const body = req.postDataJSON(); writes.push({p, body}); providers = {...providers, enabled: body.enabled, default: {model: body.model, runtime: body.model ? 'codex' : ''}, revision: providers.revision + 1}; }
        return json(providers);
      }
      if (p === '/api/v2/computers/computer-1/revoke') { writes.push({p, body: req.postDataJSON()}); computers[0].revoked_at = new Date().toISOString(); return json({revoked: true}); }
      if (p === '/api/v2/chat/botops') { writes.push({p, body: req.postDataJSON()}); return json({}); }
      if (p === '/api/v2/access') return json({people: [{id: 'ana', name: 'Ana', owner: true, role: 'owner', can_sign_in: true}], home_domain: 'example.com', allowed: [], allowed_domains: [], owner: {}, rules: {}});
      if (p === '/api/v2/me/tokens') return json({tokens: []});
      if (p === '/api/v2/slack/app') return json({configured: false});
      if (p === '/api/v2/meeting-importers') return json({importers: [], computers: []});
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      return json({});
    });
    await page.goto('https://tico-ui.test/#/integrations');
    const org = page.locator('[data-gh-form] input[name=org]'), name = page.locator('[data-gh-form] input[name=name]');
    await org.fill('acme-inc'); await name.fill('Acme Tico'); await name.focus();
    // The app's polls, and a redraw of the same route (the Tools page rebuilt itself and wiped the form).
    // route() decides to keep or redraw the busy page before it returns, so the values can be read at once.
    await page.evaluate(async () => { await refresh(false); await refresh(true); applyConfig(await get('/v2/config')); route(); });
    assert.equal(await org.inputValue(), 'acme-inc', 'Organization survives the refresh');
    assert.equal(await name.inputValue(), 'Acme Tico', 'App name survives the refresh');

    // Settings: loadSettings and a same-route redraw leave the Add computer and API token fields alone.
    await page.goto('https://tico-ui.test/#/settings');
    const machine = page.locator('#machine-label'), token = page.locator('#settings-token-form input[name=label]');
    await machine.waitFor(); await token.waitFor();
    await machine.fill('Build server'); await token.fill('CI script');
    // The same-route redraw starts its own loadSettings; wait for that one to finish too.
    await page.evaluate(async () => {
      await loadSettings();
      const real = loadSettings; let again = null;
      loadSettings = () => (again = real());
      try { route(); } finally { loadSettings = real; }
      await again;
    });
    assert.equal(await machine.inputValue(), 'Build server', 'Computer name survives loadSettings');
    assert.equal(await token.inputValue(), 'CI script', 'Token label survives loadSettings');
    await page.locator('[data-settings-tab=bots]').click();
    assert.equal(await page.locator('#settings-bots #settings-add-bot').isVisible(), true);
    assert.equal(await page.locator('#settings-devices #settings-add-bot').count(), 0);
    // Bots search: it filters as typed, keeps the box and the cursor through a reload, and a bot it hides
    // drops out of the selection, so a bulk change never reaches a bot that is not on screen.
    const botSearch = page.locator('[data-bots-search]');
    await page.locator('[data-bot-pick=helper]').check();
    await botSearch.pressSequentially('HELP');
    assert.equal(await page.locator('[data-bots-count]').innerText(), '1 bot');
    await botSearch.press('x');
    await page.evaluate(() => loadSettings());
    assert.equal(await botSearch.inputValue(), 'HELPx', 'Search survives loadSettings');
    assert.equal(await page.evaluate(() => document.activeElement?.matches('[data-bots-search]')), true);
    assert.equal(await page.locator('#set-bots .empty').innerText(), 'No bots match “HELPx”.');
    assert.equal(await page.evaluate(() => SETTINGS_BOTS_VIEW.selected.size), 0, 'a hidden bot leaves the selection');
    await page.locator('[data-bots-search-clear]').click();
    assert.equal(await page.locator('[data-settings-bot]').count(), 1);
    assert.equal(await botSearch.inputValue(), '');
    await page.locator('#settings-more > summary').click();
    await page.locator('[data-settings-tab=recurring]').click();
    await page.locator('#set-recurring [data-new-routine]').click();
    assert.equal(await page.locator('#routine-editor select[name=bot]').inputValue(), 'helper');
    await page.locator('#routine-editor [data-routine-close]').first().click();
    await page.locator('[data-settings-tab=providers]').click();
    await page.locator('#set-prov-save').waitFor();
    assert.equal(await page.locator('#set-providers [data-model-login]').isVisible(), true);
    assert.equal(await page.locator('#set-providers a[href="#/credentials"]').isVisible(), true);
    await page.locator('#set-prov [data-provider]').uncheck();
    await page.locator('#set-prov-save').click();
    await page.waitForFunction(() => !S.config.providers_configured);
    assert.deepEqual(writes.find(row => row.p === '/api/v2/providers').body.enabled, []);
    await page.locator('#set-providers .muted', {hasText: 'Bots wait'}).waitFor();
    assert.match(await page.locator('#set-providers').innerText(), /Bots wait/);
    await page.locator('[data-settings-tab=people]').click();
    await page.locator('#people-add').waitFor();
    await page.locator('[data-settings-tab=devices]').click();
    await page.locator('#set-machines .people-more').click();
    await page.locator('[data-computer-remove]').click();
    await page.waitForFunction(() => !!SETTINGS_DATA.machines[0].revoked_at);
    assert.ok(writes.some(row => row.p === '/api/v2/computers/computer-1/revoke'));
    assert.match(await page.locator('#machine-enroll-status').innerText(), /docker compose/);
    assert.equal(await page.evaluate(() => apiError({error: {detail: 'start the title with a verb (it starts "Budget"). Rewrite the title/body to fix these writing errors and retry. Do not ask the human to waive formatting rules.'}}, {})), 'Start the title with an action, such as “Email” or “Review”. Please edit the title or details and try again.');
    assert.equal(await page.evaluate(() => taskBody({body: 'Read the report.\n\nAttachments (untrusted source material; authenticated downloads):\n- report.txt: /api/v2/blobs/123', attachments: [{name: 'report.txt'}]})), 'Read the report.');
    await page.evaluate(() => openTaskCreate('human:ana'));
    assert.equal(await page.locator('#task-create-form textarea[name=body]').getAttribute('required'), '');
    await page.evaluate(() => document.querySelector('#task-create-form').closest('dialog').close());
    await page.evaluate(() => settingsEditInstructions('helper'));
    await page.locator('textarea[name=changes]').fill('Use the updated support policy.');
    await page.locator('dialog[open] [type=submit]').click();
    await page.waitForURL('**/#/bot/botops/chat');
    assert.match(writes.find(row => row.p === '/api/v2/chat/botops').body.text, /Instructions \(AGENT.md\).*Helper/);
    assert.deepEqual(errors, []);
    console.log('ok');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
