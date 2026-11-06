// Bot editor keeps a bot's saved held model (gpt-6.1-terra, Dev 693): the Model list offers it with its efforts, the
// effort can change on it, and a bot on another model is not offered it. The server lists the held row only while a
// bot already runs it (backend/app.py /api/v2/models), so this fixture does the same.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
(async () => {
  const browser = await chromium.launch({headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 900}, serviceWorkers: 'block'});
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    const model = (id, harness, provider, extra = {}) => ({id, label: id.replace(/-/g, ' '), runtime: harness, provider,
      efforts: ['low', 'high'], default_effort: 'high', harnesses: [harness], ...extra});
    const terra = model('gpt-6.1-terra', 'codex', 'openai', {efforts: ['low', 'medium', 'high'], default_effort: 'medium',
      deprecated: true, held: true});
    const catalogModels = [model('gpt-6-1-sol', 'codex', 'openai'), model('opus-5-5', 'claude', 'anthropic')];
    const helper = {name: 'helper', display_name: 'Helper', description: '', status: 'active', owner_id: 'ana', repo: 'bot-helper',
      can_manage: true, users: [], bot_owners: [], revision: 4, model: 'gpt-6.1-terra', harness: 'codex', reasoning_effort: 'medium',
      thread_mode: 'personal', bot_contact: 'open', machine: {runner_id: 'r1', label: 'Box', owner_id: 'ana', generation: 7}};
    const scout = {...helper, id: 'scout', slug: 'scout', name: 'scout', display_name: 'Scout', repo: 'bot-scout', revision: 1,
      model: 'opus-5-5', harness: 'claude', reasoning_effort: 'high'};
    const bots = [helper, scout];
    const posts = [];
    const catalog = {harnesses: [{id: 'codex', label: 'Codex (OpenAI)', runtime: 'codex'},
      {id: 'claude', label: 'Claude Code (Anthropic)', runtime: 'claude', capabilities: {compaction: true}}],
      default: {runtime: 'claude', model: 'opus-5-5'}};
    // As the server answers: the held row is listed only while some bot runs it.
    const listed = () => [...catalogModels, ...(bots.some(bot => bot.model === terra.id) ? [terra] : [])];
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
      if (p === '/api/v2/operations') return json({machines: [{id: 'r1', label: 'Box', owner_id: 'ana', bots: ['helper', 'scout']}], services: []});
      if (p === '/api/v2/models') return json({...catalog, models: listed(), enabled_providers: ['openai', 'anthropic']});
      if (p === '/api/v2/providers') return json({enabled: ['openai', 'anthropic'], default: catalog.default, revision: 1,
        providers: [{id: 'openai', label: 'OpenAI'}, {id: 'anthropic', label: 'Anthropic'}]});
      if (req.method() === 'POST' && p === '/api/v2/bots/helper/definition') {
        posts.push(['definition', req.postDataJSON()]);
        return json({name: 'helper', display_name: 'Helper', revision: 5});
      }
      if (req.method() === 'POST' && p === '/api/v2/bots/helper/transitions') {
        posts.push(['transitions', req.postDataJSON()]);
        return json({id: 'tr-1', bot: 'helper', kind: 'model', state: 'preparing'});
      }
      if (p === '/api/v2/settings/transitions/tr-1') return json({id: 'tr-1', bot: 'helper', kind: 'model', state: 'preparing',
        progress: {prepared: 0, total: 1}, target: {harness: 'codex', model: 'gpt-6.1-terra', effort: 'low'}});
      if (p === '/api/v2/usage/limits') return json({bots: {}});
      if (p === '/api/v2/settings/history') return json({changes: [], transitions: []});
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      return json(unrouted(p));
    });
    const editor = page.locator('#bot-editor');
    const row = kind => editor.locator(`[data-bot-choice][data-kind=${kind}]`);
    const openEditor = async name => {
      await page.waitForFunction(() => typeof S !== 'undefined' && S.emps?.length === 2 && SETTINGS_DATA.models?.length);
      await page.evaluate(slug => settingsEditBot(slug), name);
      await page.locator('#pane-settings').waitFor();
      await page.waitForFunction(() => !!document.querySelector('#bot-editor [data-bot-status]'));
      await page.evaluate(slug => settingsEditBot(slug, true), name);
      await row('model').locator('[data-choice-model]').waitFor();
    };
    const optionValues = locator => locator.locator('option').evaluateAll(options => options.map(option => option.value));

    await page.goto('https://tico-ui.test/#/settings');

    // The bot on the held Terra: its own model is offered, with its efforts, and the effort select is live.
    await openEditor('helper');
    assert.ok((await optionValues(row('model').locator('[data-choice-model]'))).includes('gpt-6.1-terra'),
      'the bot on the held model sees it in its Model list');
    assert.deepEqual(await optionValues(row('model').locator('[data-choice-effort]')), ['low', 'medium', 'high'],
      'the held model keeps its efforts');
    assert.equal(await row('model').locator('[data-choice-effort]').isDisabled(), false, 'the effort can be changed');
    await row('model').locator('[data-choice-effort]').selectOption('low');
    const picked = await page.evaluate(() => {
      const box = document.querySelector('#bot-editor [data-bot-choice][data-kind=model]');
      return {changed: box.picker.changed(), valid: box.picker.valid(), value: box.picker.value()};
    });
    assert.deepEqual({changed: picked.changed, valid: picked.valid}, {changed: true, valid: true},
      'an effort change on the held model is a valid pick');
    assert.equal(picked.value, 'codex::gpt-6.1-terra::low');
    assert.deepEqual(errors, [], 'no page errors');
    // Save takes the effort change through the model transition, which the server accepts for the same held model.
    await editor.locator('button[type=submit]').click();
    const transition = page.locator('#transition-dialog');
    await transition.locator('[data-confirm-prepare]').waitFor();
    await transition.locator('[data-confirm-prepare]').click();
    for (let i = 0; i < 100 && !posts.some(([what]) => what === 'transitions'); i++) await page.waitForTimeout(50);
    const sent = posts.find(([what]) => what === 'transitions');
    assert.ok(sent, 'Save starts the model transition for the effort change');
    assert.equal(sent[1].model, 'gpt-6.1-terra');
    assert.equal(sent[1].effort, 'low');

    // Another bot, on another model, is not offered the held row.
    await page.reload();
    await openEditor('scout');
    assert.equal((await optionValues(row('model').locator('[data-choice-model]'))).includes('gpt-6.1-terra'), false,
      'a bot on another model is not offered the held row');
    assert.deepEqual(errors, [], 'no page errors on the second bot');
  } finally {
    await browser.close();
  }
  console.log('held-model-editor: ok');
})().catch(error => { console.error(error); process.exit(1); });
