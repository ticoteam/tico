// Offline browser regression: a Settings or Tools form keeps what a person has typed while the app's
// polls run. Types into Library › Tools › GitHub (its GitHub App card), and into Settings > Computers (Add computer, API token), fires the
// refresh paths and asserts the values survive.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile, harnesses} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 900}, serviceWorkers: 'block'});
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    const writes = [];
    const computers = [{id: 'computer-1', label: 'Build server', owner_id: 'ana', bots: ['helper'], platform: 'linux',
      last_seen_at: new Date().toISOString(), readiness: {runtimes: {codex: {installed: true, authenticated: 'missing'}}}}];
    let providers = {enabled: ['openai'], default: {runtime: 'codex', model: 'test-model'}, revision: 1,
      providers: [{id: 'openai', label: 'OpenAI', recommended: 'test-model'}]};
    const bots = [{name: 'helper', display_name: 'Helper', status: 'active', owner_id: 'ana', can_manage: true, users: [], bot_owners: []}];
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
      const github = {service: 'github', title: 'GitHub', kind: 'cli', summary: 'Code', access: 'gh CLI', credentials: ['GH_TOKEN'], declared_as: '',
        writes: 'allowed', owner_id: 'Ana', aliases: [], status: 'not_configured', bots: [], connections: []};
      if (p === '/api/v2/tools') return json({integrations: [github]});
      if (p === '/api/v2/tools/github') return json({...github, queries: [], body: '', learnings: []});
      if (p === '/api/v2/github/app') return json({connected: false});
      if (p === '/api/v2/operations') return json({machines: computers, services: []});
      if (p === '/api/v2/models') return json({models: [{id: 'test-model', label: 'Test model', provider: 'openai', runtime: 'codex'}], harnesses});
      if (p === '/api/v2/providers') {
        if (req.method() === 'PUT') { const body = req.postDataJSON(); writes.push({p, body}); providers = {...providers, enabled: body.enabled, default: {model: body.model, runtime: body.model ? 'codex' : ''}, revision: providers.revision + 1}; }
        return json(providers);
      }
      if (p === '/api/v2/computers/computer-1/revoke') { writes.push({p, body: req.postDataJSON()}); computers[0].revoked_at = new Date().toISOString(); return json({revoked: true}); }
      if (p === '/api/v2/assistant/messages') { writes.push({p, body: req.postDataJSON()}); return json({}); }
      if (p === '/api/v2/access') return json({people: [{id: 'ana', name: 'Ana', owner_id: true, role: 'owner', can_sign_in: true}], home_domain: 'example.com', allowed: [], allowed_domains: [], owner_id: {}, rules: {}});
      if (p === '/api/v2/me/tokens') return json({tokens: []});
      if (p === '/api/v2/slack/app') return json({configured: false});
      if (p === '/api/v2/meeting-importers') return json({importers: [], computers: []});
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      return json(unrouted(p));
    });
    await page.goto('https://tico-ui.test/#/library/tools/github');
    const org = page.locator('[data-gh-form] input[name=org]'), name = page.locator('[data-gh-form] input[name=name]');
    await org.fill('acme-inc'); await name.fill('Acme Tico'); await name.focus();
    // The app's polls, and a redraw of the same route (the old Tools page rebuilt itself and wiped the form).
    // route() decides to keep or redraw the busy page before it returns, so the values can be read at once.
    await page.evaluate(async () => { await refresh(false); await refresh(true); applyConfig(await get('/v2/config')); route(); });
    assert.equal(await org.inputValue(), 'acme-inc', 'Organization survives the refresh');
    assert.equal(await name.inputValue(), 'Acme Tico', 'App name survives the refresh');

    // Settings: loadSettings and a same-route redraw leave the Add computer and API token fields alone.
    await page.goto('https://tico-ui.test/#/settings/devices');
    const machine = page.locator('#machine-label'), token = page.locator('#settings-token-form input[name=label]');
    await token.waitFor();
    await token.fill('CI script');
    await page.locator('#machine-add-open').click();
    await machine.fill('Build server');
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
    assert.equal(await page.locator('#machine-add-dialog').evaluate(d => d.open), true, 'Add computer stays open through loadSettings');
    await page.keyboard.press('Escape');
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
    // Escape empties the box and keeps the cursor there.
    await botSearch.fill('zzz');
    await botSearch.press('Escape');
    assert.equal(await botSearch.inputValue(), '');
    assert.equal(await page.locator('[data-settings-bot]').count(), 1);
    const focused = () => page.evaluate(() => { const a = document.activeElement; return a?.dataset.botsPicking !== undefined ? a.textContent : a?.dataset.botsSearch !== undefined ? 'search' : a?.tagName; });
    assert.equal(await focused(), 'search', 'Escape keeps the cursor in the search box');
    // On a phone, Select and Done redraw the list and keep the keyboard on the same control.
    await page.setViewportSize({width: 390, height: 844});
    assert.equal(await page.locator('[data-bots-filter],[data-bots-filters-toggle]').count(), 0, 'one search replaces the filters');
    await page.locator('[data-bots-picking]').press('Enter');
    assert.equal(await focused(), 'Done');
    await page.locator('[data-bots-pick-all]').press('Enter');
    assert.equal(await page.evaluate(() => document.activeElement?.dataset.botsPickAll !== undefined), true);
    await page.locator('[data-bots-picking]').press('Enter');
    assert.equal(await focused(), 'Select');
    assert.equal(await page.evaluate(() => SETTINGS_BOTS_VIEW.selected.size), 0);
    assert.equal(await page.locator('tr[data-settings-bot=helper] .sb-cell-access').getAttribute('aria-label'), 'Access: You: See · Read · Write');
    await page.setViewportSize({width: 1440, height: 900});
    // Settings > Models > Defaults: the default fallback is on until the owner turns it off; the save says so and
    // leaves the providers as they are.
    await page.locator('[data-settings-tab=models]').click();
    await page.locator('#set-prov-save').waitFor();
    assert.equal(await page.locator('#set-prov [data-provider], #set-providers [data-model-login]').count(), 0, 'no provider boxes or sign-in prompts');
    assert.equal(await page.locator('#set-prov [data-provider-fallback]').isChecked(), true);
    await page.locator('#set-prov [data-provider-fallback]').uncheck();
    const saves = () => writes.filter(row => row.p === '/api/v2/providers').length;
    // The save redraws the form; the next edit waits for the new one.
    await page.evaluate(() => { document.querySelector('#set-prov').dataset.before = '1'; });
    await page.locator('#set-prov-save').click();
    for (let i = 0; i < 100 && !saves(); i++) await new Promise(done => setTimeout(done, 50));
    assert.equal(writes.find(row => row.p === '/api/v2/providers').body.auto_fallback, false);
    assert.equal('preset' in writes.find(row => row.p === '/api/v2/providers').body, false);
    // The check-model order was not edited, so the save leaves it alone instead of re-checking it.
    assert.equal('check_models' in writes.find(row => row.p === '/api/v2/providers').body, false);
    // Edited, the order is sent as typed.
    const before = saves();
    await page.locator('#set-prov:not([data-before]) [data-provider-check]').waitFor();
    await page.locator('#set-prov [data-provider-check]').fill('grok, codex');
    await page.locator('#set-prov-save').click();
    for (let i = 0; i < 100 && saves() === before; i++) await new Promise(done => setTimeout(done, 50));
    assert.deepEqual(writes.filter(row => row.p === '/api/v2/providers').at(-1).body.check_models, ['grok', 'codex']);
    await page.locator('[data-settings-tab=people]').click();
    await page.locator('#people-add').waitFor();
    await page.locator('[data-settings-tab=devices]').click();
    await page.locator('#set-machines .machine-name[data-computer-open=computer-1]').click();
    await page.locator('[data-computer-remove]').click();
    await page.locator('dialog.settings-confirm[open] [data-confirm-ok]').click();
    await page.waitForFunction(() => !!SETTINGS_DATA.machines[0].revoked_at);
    assert.ok(writes.some(row => row.p === '/api/v2/computers/computer-1/revoke'));
    // The presence read can expose revoked_at before the action's remaining Settings reads finish.
    await page.locator('#machine-note code', {hasText: 'docker compose'}).waitFor();
    assert.match(await page.locator('#machine-note').innerText(), /docker compose/);
    await page.locator('#set-machines .machine-card').waitFor({state: 'detached'});
    const showRevoked = page.getByRole('checkbox', {name: 'Show revoked (1)', exact: true});
    await showRevoked.check();
    assert.match(await page.locator('#set-machines .machine-card').innerText(), /Build server[\s\S]*revoked/);
    await page.evaluate(() => loadSettings());
    assert.equal(await showRevoked.isChecked(), true, 'refresh preserves the selected view');
    await showRevoked.uncheck();
    assert.equal(await page.locator('#set-machines .machine-card').count(), 0);
    assert.equal(await page.evaluate(() => apiError({error: {detail: 'start the title with a verb (it starts "Budget"). Rewrite the title/body to fix these writing errors and retry. Do not ask the human to waive formatting rules.'}}, {})), 'Start the title with an action, such as “Email” or “Review”. Please edit the title or details and try again.');
    assert.equal(await page.evaluate(() => taskBody({body: 'Read the report.\n\nAttachments (untrusted source material; authenticated downloads):\n- report.txt: /api/v2/blobs/123', attachments: [{name: 'report.txt'}]})), 'Read the report.');
    await page.evaluate(() => openTaskCreate('human:ana'));
    assert.equal(await page.locator('#task-create-form textarea[name=body]').getAttribute('required'), '');
    await page.evaluate(() => document.querySelector('#task-create-form').closest('dialog').close());
    await page.evaluate(() => settingsEditInstructions('helper'));
    await page.locator('textarea[name=changes]').fill('Use the updated support policy.');
    await page.locator('#bot-instructions-editor [type=submit]').click();
    await page.waitForFunction(() => document.querySelector('[data-instructions-status]')?.textContent === 'Changes sent.');
    assert.equal(await page.locator('dialog[open]').count(), 0);
    assert.match(writes.find(row => row.p === '/api/v2/assistant/messages').body.text, /Instructions \(AGENT.md\).*Helper/);
    assert.deepEqual(errors, []);
    console.log('ok');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
