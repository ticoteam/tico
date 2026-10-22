// A bot's task model in Settings (bot task model 2/4 and 4/4, tasks df2cf5b0 and 7a9b30d4):
//  - the bot editor has a Task model row, "Same as bot" until set; Save applies a pick (POST /v2/bots/{bot}/task-model,
//    with expected_revision) before the definition, and never the bot's own model;
//  - the bots table shows a subtle Tasks mark beside each bot's model, marked when its tasks run on another model;
//    it opens a row under that bot with the same three pickers; Apply sets the task model only, and clearing it to
//    "Same as bot" sends null;
//  - a cheaper pick on a high-stakes bot saves and shows the server's plain-words warning under the row.
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
    const model = (id, harness, provider) => ({id, label: id.replace(/-/g, ' '), runtime: harness, provider,
      efforts: ['low', 'high'], default_effort: 'high', harnesses: [harness]});
    const catalog = {
      models: [model('claude-sonnet-5-5', 'claude', 'anthropic'), model('claude-haiku-5-5', 'claude', 'anthropic'), model('gpt-6-luna', 'codex', 'openai')],
      harnesses: [{id: 'codex', label: 'Codex (OpenAI)', runtime: 'codex'}, {id: 'claude', label: 'Claude Code (Anthropic)', runtime: 'claude'}],
      default: {runtime: 'claude', model: 'claude-sonnet-5-5'},
    };
    const helper = {name: 'helper', display_name: 'Helper', description: '', status: 'active', operator: 'ana', repo: 'bot-helper',
      can_manage: true, users: [], bot_owners: [], revision: 4, model: 'claude-sonnet-5-5', harness: 'claude', reasoning_effort: 'high',
      task_model: null, thread_mode: 'personal', bot_contact: 'open', machine: {runner_id: 'r1', label: 'Box', operator: 'ana', generation: 7}};
    const bots = [helper];
    const WARNING = 'This bot sends messages for you. Haiku 5.5 is a cheaper model than its own Sonnet 5.5, and may word things badly. It still saves, and its tasks run on it.';
    let calls = [];
    await page.route('**/*', route => {
      const req = route.request(), p = new URL(req.url()).pathname;
      const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
      if (ui && fs.existsSync(uiFile(ui[1])))
        return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      if (req.method() === 'POST' && p.startsWith('/api/v2/bots/helper/')) {
        const body = req.postDataJSON(), what = p.split('/').pop();
        calls.push({what, body});
        if (body.expected_revision !== helper.revision) return json({error: {detail: 'Bot configuration changed; refresh before saving'}}, 409);
        if (what === 'task-model') {
          // As backend/settings_admin.py set_task_model answers: the stored choice, and a warning for a cheaper pick.
          const t = body.task_model;
          helper.task_model = t ? {harness: t.harness, model: t.model, reasoning_effort: t.effort, runtime: t.harness} : null;
          return json({bot: 'helper', task_model: helper.task_model, revision: ++helper.revision, readiness: null,
            warning: t?.model === 'claude-haiku-5-5' ? WARNING : ''});
        }
        if (what === 'definition') {
          Object.assign(helper, {display_name: body.display_name, description: body.description});
          return json({name: 'helper', display_name: helper.display_name, revision: ++helper.revision});
        }
      }
      if (p === '/api/me') return json({id: 'ana', role: 'owner', name: 'Ana', email: 'ana@example.com', cloud: true, registered: true});
      if (p === '/api/v2/config') return json({version: '0.1.0', app_name: 'Tico', local: true});
      if (p === '/api/employees') return json(bots);
      if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana'}], teams: {}});
      if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: []});
      if (p === '/api/v2/status') return json({bots: []});
      if (p === '/api/v2/operations') return json({machines: [{id: 'r1', label: 'Box', operator: 'ana', bots: ['helper']}], services: []});
      if (p === '/api/v2/models') return json({...catalog, enabled_providers: ['openai', 'anthropic']});
      if (p === '/api/v2/providers') return json({enabled: ['openai', 'anthropic'], default: catalog.default, revision: 1,
        providers: [{id: 'openai', label: 'OpenAI'}, {id: 'anthropic', label: 'Anthropic'}]});
      if (p === '/api/v2/usage/limits') return json({bots: {}});
      if (p === '/api/v2/settings/history') return json({changes: [], transitions: []});
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      return json(unrouted(p));
    });
    const loaded = revision => page.waitForFunction(r => S.emps.find(e => e.name === 'helper')?.revision === r, revision);
    await page.goto('https://tico-ui.test/#/settings');
    await page.waitForFunction(() => typeof S !== 'undefined' && S.emps?.some(e => e.name === 'helper') && SETTINGS_DATA.models?.length);

    // 1. The bot editor: a Task model row, "Same as bot" by default; Save applies a pick before the definition.
    const editor = page.locator('#bot-editor');
    const task = editor.locator('[data-bot-choice][data-kind=task]');
    const openEditor = async () => {
      await loaded(helper.revision);
      await page.evaluate(() => settingsEditBot('helper'));
      await page.locator('#pane-settings').waitFor();
      await page.waitForFunction(() => !!document.querySelector('#bot-editor [data-bot-status]'));
      await page.evaluate(() => settingsEditBot('helper', true));
      await task.locator('[data-choice-harness]').waitFor();
      calls = [];
    };
    await openEditor();
    assert.equal(await task.locator('[data-choice-harness] option:checked').innerText(), 'Same as bot', 'defaults to the same as the bot');
    assert.equal(await task.locator('[data-choice-model]').isDisabled(), true);
    await task.locator('[data-choice-harness]').selectOption('codex');
    await task.locator('[data-choice-model]').selectOption('gpt-6-luna');
    await editor.locator('button[type=submit]').click();
    await page.waitForFunction(() => document.querySelector('#bot-editor [data-bot-status]')?.textContent === 'Saved');
    assert.deepEqual(calls.map(c => c.what), ['task-model', 'definition'], 'the task model, then the definition; never the bot model');
    assert.deepEqual(calls[0].body, {task_model: {harness: 'codex', model: 'gpt-6-luna', effort: 'high'}, expected_revision: 4});
    assert.equal(calls[1].body.expected_revision, 5, 'the definition follows the revision the task model made');
    await openEditor();
    assert.equal(await task.locator('[data-choice-model]').inputValue(), 'gpt-6-luna', 'reopening shows the task model');
    assert.equal(helper.model, 'claude-sonnet-5-5', "the bot's own model is unchanged");
    await page.evaluate(() => settingsEditBot('helper', true));

    // 2. The bots table: the Tasks mark beside the model is marked while tasks run on another model.
    const toBots = async () => {
      await page.evaluate(() => { location.hash = '#/settings'; });
      await page.waitForFunction(() => S.route === SETTINGS && !!document.querySelector('#set-bots'));
      await page.evaluate(() => { settingsShow('bots'); renderSettingsBots(); });
    };
    await toBots();
    const mark = page.locator('[data-settings-bot=helper] [data-task-open=helper]');
    await mark.waitFor();
    assert.equal(await mark.evaluate(b => b.classList.contains('on')), true, 'marked: tasks run on gpt-6-luna');
    assert.match(await mark.getAttribute('title'), /Tasks run on/);
    await mark.click();
    const row = page.locator('tr[data-task-row=helper]');
    await row.waitFor();
    assert.equal(await row.evaluate(tr => tr.previousElementSibling?.dataset.settingsBot), 'helper', 'the row opens under its bot');
    const pickers = row.locator('[data-bot-choice][data-kind=task]');
    assert.equal(await pickers.locator('[data-choice-model]').inputValue(), 'gpt-6-luna');
    // Clearing it returns to the same as the bot.
    calls = [];
    await pickers.locator('[data-choice-harness]').selectOption('');
    await pickers.locator('[data-choice-apply]').click();
    await loaded(7);
    assert.deepEqual(calls, [{what: 'task-model', body: {task_model: null, expected_revision: 6}}]);
    await page.waitForFunction(() => !document.querySelector('[data-task-open=helper]')?.classList.contains('on'));
    assert.equal(await row.count(), 1, 'the row stays open after Apply');

    // 4/4. A cheaper pick on a high-stakes bot saves, and the row shows the warning in plain words.
    calls = [];
    await pickers.locator('[data-choice-harness]').selectOption('claude');
    await pickers.locator('[data-choice-model]').selectOption('claude-haiku-5-5');
    await pickers.locator('[data-choice-apply]').click();
    await loaded(8);
    assert.equal(calls[0].body.task_model.model, 'claude-haiku-5-5');
    await row.locator('[data-task-warning]').waitFor();
    assert.equal(await row.locator('[data-task-warning]').innerText(), WARNING);
    assert.equal(await mark.evaluate(b => b.classList.contains('on')), true, 'it saved');
    // The editor shows the same warning under its Task model row.
    await openEditor();
    assert.equal(await editor.locator('[data-task-warning]').innerText(), WARNING);
    await page.evaluate(() => settingsEditBot('helper', true));

    // Close folds the row away and gives the keyboard back to the mark.
    await toBots();
    await page.locator('[data-task-close=helper]').click();
    await page.waitForFunction(() => !document.querySelector('tr[data-task-row=helper]'));
    assert.equal(await page.evaluate(() => document.activeElement?.dataset.taskOpen), 'helper');

    // A phone: the row stacks its pickers under the bot.
    await page.setViewportSize({width: 390, height: 844});
    await page.locator('[data-task-open=helper]').click();
    await row.waitFor();
    const width = await row.evaluate(tr => tr.getBoundingClientRect().width);
    assert.ok(width <= 390, `the row fits the phone (${width}px)`);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'no sideways scroll');

    assert.deepEqual(errors, []);
    console.log('ok');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
