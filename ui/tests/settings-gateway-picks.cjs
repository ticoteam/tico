// A Gateway bot's model is a pick (Settings > Bots): Department · Level · Fast · Version under the Tico AI Gateway, one
// muted line with what runs and the price, the department from the bot's group, old ids as "(old lineup)"; Settings >
// Gateway lists the picks in use and the services in two flat tables; Settings > Computers > Models has the Decisions
// level. The catalog is the one the server sends (backend/gateway_tiers.py). With TICO_SHOTS set, it saves screenshots there.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const {html, uiFile, harnesses} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const shotsDir = process.env.TICO_SHOTS || '';
(async () => {
  const served = JSON.parse(execFileSync(process.env.TICO_PYTHON || 'python3', ['-c',
    'import json; from backend import providers, gateway_tiers; print(json.dumps({"models": list(providers.MODEL_CATALOG), "gateway": gateway_tiers.view()}))'],
    {cwd: path.join(__dirname, '../..')}).toString());
  // Finance Medium has a newer 1.1 than every other tier: a pin to it has nowhere to go but its own tier.
  const medium = served.gateway.tiers['tico-finance-medium'];
  medium.versions.unshift({...medium.versions[0], version: '1.1', id: 'tico-finance-medium-1.1'});
  medium.current = '1.1';
  const browser = await chromium.launch({headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1280, height: 900}, serviceWorkers: 'block'});
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    const catalog = {models: served.models, harnesses, gateway: served.gateway, default: {runtime: 'pi', model: 'tico-general-medium-1.0'}};
    const bot = (name, display, model, department, extra = {}) => ({name, display_name: display, description: '', status: 'active', owner_id: 'ana',
      repo: 'bot-' + name, can_manage: true, users: [], bot_owners: [], revision: 1, model, harness: 'pi', reasoning_effort: 'high',
      keep_tasks_going: null, task_flow: {going: 0, waiting: 0}, task_model: null, thread_mode: 'personal', bot_contact: 'open',
      gateway_department: department, machine: {runner_id: 'r1', label: 'Box', owner_id: 'ana', generation: 1}, ...extra});
    const bots = [bot('seller', 'Seller', 'tico-sales-medium-1.0', 'sales'), bot('old', 'Old Hand', 'tico-mid-1.0', 'general'),
      bot('writer', 'Writer', 'tico-marketing-low-fast', 'marketing'), bot('banker', 'Banker', 'tico-finance-medium-1.1', 'finance')];
    let decide = 'low', saved = null;
    await page.route('**/*', route => {
      const req = route.request(), p = new URL(req.url()).pathname;
      const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
      if (ui && fs.existsSync(uiFile(ui[1])))
        return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      if (p === '/api/me') return json({id: 'ana', role: 'owner', name: 'Ana', email: 'ana@example.com', cloud: true, registered: true});
      if (p === '/api/v2/config') return json({version: '0.1.0', app_name: 'Tico', local: true});
      if (p === '/api/employees') return json(bots);
      if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana'}], teams: {}});
      if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: []});
      if (p === '/api/v2/status') return json({bots: []});
      if (p === '/api/v2/operations') return json({machines: [{id: 'r1', label: 'Box', owner_id: 'ana', bots: bots.map(b => b.name)}], services: []});
      if (p === '/api/v2/models') return json({...catalog, enabled_providers: ['tico']});
      if (p === '/api/v2/providers' && req.method() === 'PUT') { saved = req.postDataJSON(); decide = saved.decide_level || decide; return json({enabled: ['tico'], default: catalog.default, revision: 2, decide_level: decide, decide_levels: ['low', 'medium', 'high']}); }
      if (p === '/api/v2/providers') return json({enabled: ['tico'], default: catalog.default, revision: 1, auto_fallback: true,
        decide_level: decide, decide_levels: ['low', 'medium', 'high'],
        providers: [{id: 'tico', label: 'Tico AI Gateway', gateway: true}]});
      if (p === '/api/v2/providers/gateway') return json({label: 'Tico AI Gateway', connected: true, source: 'settings', url: 'https://updates.tico.team/v1/gateway',
        default_url: 'https://updates.tico.team/v1/gateway', editable: true, preview: 'tgw…def', revision: 1, state: 'connected', working: true, message: 'Connected.'});
      if (p === '/api/v2/gateway') return json({connection: {connected: true, state: 'connected', working: true, spent_usd: 12.4, cap_usd: 100},
        decide_level: decide, as_of: '2026-10-25',
        picks: [{id: 'tico-sales-medium-1.0', name: 'Sales · Medium', model: 'Claude Sonnet 5.5', version: '1.0', bots: 3, price: '$2.50 / $12.50 per 1M', old: false},
          {id: 'tico-marketing-low-fast', name: 'Marketing · Low · Fast', model: 'DeepSeek V4.1 Flash', version: 'auto', bots: 1, price: '$0.45 / $1.80 per 1M', old: false},
          {id: 'tico-mid-1.0', name: 'Mid', model: 'Claude Sonnet 5.5', version: '1.0 (old lineup)', bots: 1, price: '$2.50 / $12.50 per 1M', old: true}],
        services: served.gateway.services || [
          {id: 'decide', name: 'Decide · Classify', status: 'live', price: 'From $0.06 per 1M tokens', tools: ['hub_decision_ask', 'hub_classify']},
          {id: 'search', name: 'Search', status: 'live', price: 'Tokens like Low · $0.00875 per search', tools: ['hub_gateway_search']},
          {id: 'fetch', name: 'Fetch', status: 'live', price: '$1.25 per 1,000 pages + tokens', tools: ['hub_gateway_fetch']}]});
      if (p === '/api/v2/usage/limits') return json({bots: {}});
      if (p === '/api/v2/settings/history') return json({changes: [], transitions: []});
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      return json(unrouted(p));
    });
    const shot = async name => { if (shotsDir) { fs.mkdirSync(shotsDir, {recursive: true}); await page.screenshot({path: path.join(shotsDir, name)}); } };
    await page.goto('https://tico-ui.test/#/settings');
    await page.waitForFunction(() => typeof S !== 'undefined' && S.emps?.some(e => e.name === 'seller') && SETTINGS_DATA.models?.length);
    const editor = page.locator('#bot-editor');
    const model = editor.locator('[data-bot-choice][data-kind=model]');
    const edit = async name => {
      await page.evaluate(n => settingsEditBot(n), name);
      await page.waitForFunction(() => !!document.querySelector('#bot-editor [data-bot-status]'));
      await page.evaluate(n => settingsEditBot(n, true), name);
      await model.locator('[data-choice-harness]').waitFor();
    };
    const pick = async () => model.evaluate(box => box.picker.value());

    // 1. The pick: the Gateway under the harness, the department from the group, the level, no Fast, the current version.
    await edit('seller');
    assert.equal(await model.locator('[data-choice-harness] option:checked').innerText(), 'Tico AI Gateway');
    assert.equal(await model.locator('[data-choice-model-label]').isHidden(), true, 'the model list gives way to the pick');
    assert.equal(await model.locator('[data-tier-department] option:checked').innerText(), 'Sales (from group)');
    assert.equal(await model.locator('[data-tier-level][aria-checked=true]').innerText(), 'Medium');
    assert.equal(await model.locator('[data-tier-fast]').isChecked(), false);
    assert.equal(await model.locator('[data-tier-version] option:checked').innerText(), '1.0 (current)');
    assert.equal(await model.locator('[data-tier-line]').innerText(), 'Claude Sonnet 5.5 · $2.50 / $12.50 per 1M');
    assert.equal(await pick(), 'pi::tico-sales-medium-1.0::high');
    assert.deepEqual(await model.locator('[data-tier-version] option').allInnerTexts(), ['1.0 (current)', 'Auto (always latest)']);
    assert.equal(await model.locator('[data-tier-department] option').count() >= 8, true, 'every department is offered');
    await model.scrollIntoViewIfNeeded();
    await shot('desktop-bots-gateway-bot.png');
    // Level, Fast and Version make the id; Low takes the low effort default.
    await model.locator('[data-tier-level=high]').click();
    assert.equal(await pick(), 'pi::tico-sales-high-1.0::high');
    await model.locator('[data-tier-fast]').check();
    assert.equal(await pick(), 'pi::tico-sales-high-fast-1.0::high');
    assert.match(await model.locator('[data-tier-line]').innerText(), /Claude Opus 5\.5 \(fast mode\) · \$10\.00 \/ \$50\.00 per 1M/);
    await model.locator('[data-tier-version]').selectOption('auto');
    assert.equal(await pick(), 'pi::tico-sales-high-fast::high', 'Auto is the id without a version');
    await model.locator('[data-tier-level=low]').click();
    await model.locator('[data-tier-department]').selectOption('legal');
    assert.equal(await pick(), 'pi::tico-legal-low-fast::high');
    assert.equal(await model.locator('[data-choice-effort]').inputValue(), 'high');
    // Another harness brings the model list back; choosing the Gateway again starts at the bot's department, Medium.
    await model.locator('[data-choice-harness]').selectOption('');
    assert.equal(await model.locator('[data-choice-model-label]').isVisible(), true);
    await model.locator('[data-choice-harness]').selectOption('tico');
    assert.equal(await pick(), 'pi::tico-sales-medium-1.0::high');

    // 1b. A pinned version stays only where the destination tier has it; otherwise that tier's current one. Auto stays Auto.
    await edit('banker');
    assert.equal(await pick(), 'pi::tico-finance-medium-1.1::high');
    await model.locator('[data-tier-level=high]').click();
    assert.equal(await pick(), 'pi::tico-finance-high-1.0::high', 'High has no 1.1: it takes its own current version');
    assert.equal(await model.locator('[data-tier-version] option:checked').innerText(), '1.0 (current)');
    await model.locator('[data-tier-level=medium]').click();
    assert.equal(await pick(), 'pi::tico-finance-medium-1.0::high', 'back at Medium the version 1.0 it holds exists there');
    await model.locator('[data-tier-version]').selectOption('1.1');
    await model.locator('[data-tier-fast]').check();
    assert.equal(await pick(), 'pi::tico-finance-medium-fast-1.0::high', 'Fast has no 1.1 either');
    await model.locator('[data-tier-version]').selectOption('auto');
    await model.locator('[data-tier-level=high]').click();
    assert.equal(await pick(), 'pi::tico-finance-high-fast::high', 'Auto stays Auto');

    // 2. An old-lineup id shows as it is until a control changes it, and nothing is offered of the old lineup.
    await edit('old');
    assert.equal(await model.locator('[data-choice-harness] option:checked').innerText(), 'Tico AI Gateway');
    assert.equal(await model.locator('[data-tier-version] option:checked').innerText(), 'Mid 1.0 (old lineup)');
    assert.equal(await model.locator('[data-tier-line]').innerText(), 'Claude Sonnet 5.5');   // no price here: the old row carries none
    assert.equal(await pick(), 'pi::tico-mid-1.0::high', 'untouched, the old id stays');
    assert.equal(await model.locator('[data-tier-level][aria-checked=true]').count(), 0);
    await model.locator('[data-tier-level=high]').click();
    assert.equal(await pick(), 'pi::tico-general-high-1.0::high', 'a change moves it to the department tier');
    assert.equal(await page.evaluate(() => modelWords('tico-mid-1.0')), 'Mid 1.0 (old lineup)');
    assert.equal(await page.evaluate(() => modelWords('tico-sales-medium-fast-1.0')), 'Sales · Medium · Fast 1.0');
    assert.equal(await page.evaluate(() => modelWords('tico-sales-medium')), 'Sales · Medium (auto)');
    // A tier kept auto: the table and the picker say so.
    await edit('writer');
    assert.equal(await model.locator('[data-tier-version] option:checked').innerText(), 'Auto (always latest)');
    assert.equal(await model.locator('[data-tier-fast]').isChecked(), true);

    // 3. The same pick under Fallback and Task model; the harness list still offers the others.
    await page.evaluate(() => { location.hash = '#/settings'; });
    await page.waitForFunction(() => S.route === '#/settings');   // a page change draws a frame later (its fade)
    await page.evaluate(() => { settingsShow('bots'); renderSettingsBots(); });
    await page.locator('[data-task-open=seller]').click();
    const task = page.locator('tr[data-task-row=seller] [data-bot-choice][data-kind=task]');
    await task.locator('[data-choice-harness]').selectOption('tico');
    assert.equal(await task.locator('[data-tier-department] option:checked').innerText(), 'Sales (from group)');
    assert.equal(await task.evaluate(box => box.picker.value()), 'pi::tico-sales-medium-1.0::high');

    // 4. Settings > Gateway: a status line, the picks in use, the services; one heading level; the key stays in Computers.
    await page.evaluate(() => settingsShow('gateway'));
    await page.locator('[data-gateway-picks]').waitFor();
    assert.equal(await page.locator('[data-gateway-line]').innerText(), 'Connected · $12.40 of $100.00 this month');
    assert.equal(await page.locator('#set-gateway-page h1, #set-gateway-page h3, #set-gateway-page h4').count(), 0, 'one heading level');
    assert.deepEqual(await page.locator('[data-gateway-picks] thead th').evaluateAll(t => t.map(x => x.textContent)), ['Pick', 'Model', 'Version', 'Bots', 'Price']);
    assert.deepEqual(await page.locator('[data-gateway-pick=tico-marketing-low-fast] td').allInnerTexts(),
      ['Marketing · Low · Fast', 'DeepSeek V4.1 Flash', 'Auto', '1', '$0.45 / $1.80 per 1M']);
    assert.equal(await page.locator('[data-gateway-pick="tico-mid-1.0"] .gw-version').innerText(), '1.0 (old lineup)');
    assert.ok(await page.locator('[data-gateway-service]').count() >= 3);
    assert.equal(await page.locator('[data-gateway-service=search] .gw-tools').innerText(), 'hub_gateway_search');
    assert.equal(await page.locator('[data-gateway-service=fetch] .gw-tools').innerText(), 'hub_gateway_fetch');
    assert.equal(await page.locator('[data-gateway-key-link]').getAttribute('href'), '#/settings/models');
    await shot('desktop-gateway.png');

    // 5. Decisions: a company level in Settings > Models > Defaults, saved with the rest.
    await page.evaluate(() => settingsShow('models'));
    const level = page.locator('#set-prov [data-provider-decide]');
    await level.waitFor();
    assert.equal(await level.inputValue(), 'low');
    assert.deepEqual(await level.locator('option').allInnerTexts(), ['Low', 'Medium', 'High']);
    await level.selectOption('high');
    await page.locator('#set-prov-save').click();
    for (let i = 0; i < 100 && !saved; i++) await page.waitForTimeout(30);
    assert.equal(saved.decide_level, 'high');
    const options = await page.locator('[data-provider-model] option').allInnerTexts();
    assert.ok(options.length <= 6 && options.some(o => o.startsWith('General')), `the company default offers General only (${options.length} options)`);

    // 6. A phone: the pick and the page fit.
    await page.setViewportSize({width: 390, height: 844});
    await page.goto('https://tico-ui.test/#/settings');
    await page.reload();
    await page.waitForFunction(() => typeof S !== 'undefined' && S.emps?.some(e => e.name === 'seller') && SETTINGS_DATA.models?.length);
    await edit('seller');
    await model.scrollIntoViewIfNeeded();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'no sideways scroll on the bot editor');
    await shot('phone-bots-gateway-bot.png');
    await page.evaluate(() => { location.hash = '#/settings'; });
    await page.waitForFunction(() => S.route === SETTINGS && !!document.querySelector('#settings-tabs'));
    await page.evaluate(() => settingsShow('gateway'));
    await page.locator('[data-gateway-picks]').waitFor();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'no sideways scroll on Settings > Gateway');
    await shot('phone-gateway.png');
    assert.deepEqual(errors, []);
    console.log('ok');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
