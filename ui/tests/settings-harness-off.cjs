// A harness whose provider is off still appears in the picker, greyed out with its way in:
// with only OpenAI and Anthropic enabled, Muse Code shows as "(off)" plus an AI-providers line,
// and enabling Meta makes it pickable with the Wonder models in its model list.
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
    const museModel = id => ({id, label: id.replace(/-/g, ' '), runtime: 'musecode', provider: 'meta',
      efforts: ['low', 'high'], default_effort: 'high', harnesses: ['musecode']});
    const catalog = {
      models: [
        {id: 'gpt-6-luna', label: 'gpt 6 luna', runtime: 'codex', provider: 'openai',
          efforts: ['low', 'high'], default_effort: 'high', harnesses: ['codex']},
        {id: 'opus-5-5', label: 'opus 5.5', runtime: 'claude', provider: 'anthropic',
          efforts: ['low', 'high'], default_effort: 'high', harnesses: ['claude']},
        museModel('muse-spark-1.3'), museModel('muse-wonder-1.0'), museModel('muse-wonder-full-1.0'),
        {id: 'hermes-profile', label: "profile's own model", runtime: 'hermes', provider: '',
          efforts: ['as-configured'], default_effort: 'as-configured', harnesses: ['hermes']},
        {id: 'gpt-old', label: 'gpt old', runtime: 'codex', provider: 'openai', deprecated: true,
          efforts: ['low'], default_effort: 'low', harnesses: ['codex']},
      ],
      harnesses: [
        {id: 'codex', label: 'Codex (OpenAI)', runtime: 'codex'},
        {id: 'claude', label: 'Claude Code (Anthropic)', runtime: 'claude'},
        {id: 'musecode', label: 'Muse Code (Meta)', runtime: 'musecode'},
        {id: 'gemini', label: 'gemini', runtime: 'gemini'},
        {id: 'hermes', label: 'hermes', runtime: 'hermes', external: true},
      ],
      default: {runtime: 'codex', model: 'gpt-6-luna'},
    };
    let enabled = ['openai', 'anthropic'];
    const bots = [{name: 'helper', display_name: 'Helper', status: 'active', owner_id: 'ana',
      can_manage: true, users: [], bot_owners: [], revision: 1, model: 'muse-spark-1.3', harness: 'musecode',
      reasoning_effort: 'high'}];
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
      if (p === '/api/issues') return json([]);
      if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: []});
      if (p === '/api/v2/status') return json({bots: []});
      if (p === '/api/v2/operations') return json({machines: [], services: []});
      if (p === '/api/v2/models') return json({...catalog, enabled_providers: enabled});
      if (p === '/api/v2/providers') return json({enabled, default: catalog.default, revision: 1,
        auto_fallback: true, providers: [{id: 'openai', label: 'OpenAI'}, {id: 'anthropic', label: 'Anthropic'}, {id: 'meta', label: 'Meta'}]});
      if (p === '/api/v2/usage/limits') return json({bots: {}});
      if (p === '/api/v2/settings/history') return json({changes: [], transitions: []});
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      return json(unrouted(p));
    });
    const openAdd = async () => {
      await page.locator('[data-settings-tab=bots]').click();
      await page.locator('#settings-add-bot').click();
      await page.locator('[data-recruit-route=scratch]').click();
      await page.locator('#recruit-picker [name=title]').fill('Harness test bot');
      await page.locator('#recruit-picker [name=brief]').fill('Check the available harness choices for this new bot.');
      await page.locator('#recruit-picker [type=submit]').click();
      await page.locator('[data-add-choice] [data-choice-harness]').waitFor();
    };
    const harnessOptions = () => page.locator('[data-add-choice] [data-choice-harness] option').evaluateAll(
      options => options.map(o => ({value: o.value, text: o.textContent, disabled: o.disabled})));
    await page.goto('https://tico-ui.test/#/settings');
    // The fixture switches providers behind the page's back, so a settings read already in flight (a dialog
    // closing reloads settings) must finish first: finishing later, or shared with the next read, it would
    // put the old provider list back.
    await page.evaluate(() => {
      const load = loadSettings, running = window.settingsLoads = new Set();
      loadSettings = (...args) => { const p = load(...args); running.add(p); p.finally(() => running.delete(p)); return p; };
    });
    const settle = () => page.evaluate(() => Promise.allSettled([...window.settingsLoads]));
    await openAdd();
    let options = await harnessOptions();
    assert.ok(options.some(o => o.value === 'codex' && !o.disabled), 'Codex is offered');
    assert.ok(options.some(o => o.value === 'claude' && !o.disabled), 'Claude Code is offered');
    const muse = options.find(o => o.value === 'musecode');
    assert.ok(muse, 'Muse Code is listed while Meta is off');
    assert.equal(muse.disabled, true, 'Muse Code is not pickable while Meta is off');
    assert.match(muse.text, /Muse Code \(off\)/, 'Muse Code says it is off');
    assert.ok(!options.some(o => o.value === 'gemini'), 'a harness with no models stays out');
    assert.ok(options.some(o => o.value === 'hermes' && !o.disabled), 'external harnesses are unaffected');
    const note = page.locator('[data-add-choice] [data-choice-off]');
    await note.waitFor();
    assert.match(await note.innerText(), /Muse Code.*Models/, 'the note names Muse Code and Models');
    await note.locator('[data-off-providers]').click();
    await page.locator('#set-providers').waitFor();
    await page.evaluate(() => document.querySelector('#bot-editor').close());
    // Enabling Meta makes Muse Code pickable and lists the Wonder models under it.
    await settle();
    enabled = ['openai', 'anthropic', 'meta'];
    await page.evaluate(() => loadSettings());
    await openAdd();
    options = await harnessOptions();
    assert.ok(options.some(o => o.value === 'musecode' && !o.disabled), 'Muse Code is pickable once Meta is on');
    assert.equal(await page.locator('[data-add-choice] [data-choice-off]').count(), 0, 'no off line once enabled');
    await page.locator('[data-add-choice] [data-choice-harness]').selectOption('musecode');
    const models = await page.locator('[data-add-choice] [data-choice-model] option').evaluateAll(
      options => options.map(o => o.value));
    assert.ok(models.includes('muse-spark-1.3'), 'Spark is listed under Muse Code');
    assert.ok(models.includes('muse-wonder-1.0'), 'Wonder is listed under Muse Code');
    assert.ok(models.includes('muse-wonder-full-1.0'), 'Wonder Full is listed under Muse Code');
    // A member gets no way in, just the clean list: no off harness, no off line.
    await settle();
    enabled = ['openai', 'anthropic'];
    await page.evaluate(() => document.querySelector('#bot-editor').close());
    await page.evaluate(() => { S.me.role = 'member'; return loadSettings(); });
    await openAdd();
    options = await harnessOptions();
    assert.ok(!options.some(o => o.value === 'musecode'), 'members never see a provider-off harness');
    assert.equal(await page.locator('[data-add-choice] [data-choice-off]').count(), 0, 'members get no off line');
    await page.evaluate(() => { S.me.role = 'owner'; });
    // A bot already on a switched-off harness keeps one row for it, not a current+off pair.
    await settle();
    enabled = ['openai', 'anthropic'];
    await page.evaluate(() => loadSettings());
    const solo = await page.evaluate(() => {
      document.querySelector('#bot-editor')?.close();
      document.querySelector('#t')?.remove();
      document.body.insertAdjacentHTML('beforeend',
        `<div id="t">${settingsChoiceCombo(S.emps.find(e => e.name === 'helper'), 'model')}</div>`);
      settingsWireCombos(document.querySelector('#t'));
      return [...document.querySelectorAll('#t [data-choice-harness] option')]
        .map(o => ({value: o.value, text: o.textContent, disabled: o.disabled, selected: o.selected}));
    });
    assert.equal(solo.filter(o => o.value === 'musecode').length, 1, 'one Muse Code row for a bot on it');
    assert.deepEqual(solo.find(o => o.value === 'musecode'), {value: 'musecode', text: 'Muse Code (Meta)',
      disabled: true, selected: true}, 'it keeps the current row selected');
    assert.deepEqual(errors, []);
    console.log('ok');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
