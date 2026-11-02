// A bot's task model in Settings (bot task model 2/4 and 4/4, tasks df2cf5b0 and 7a9b30d4):
//  - the bot editor has a Task model row, "Same as bot" until set; Save applies a pick (POST /v2/bots/{bot}/task-model,
//    with expected_revision) before the definition, and never the bot's own model;
//  - the bots table shows a subtle Tasks mark beside each bot's model, marked when its tasks run on another model;
//    it opens a row under that bot with the same three pickers; Apply sets the task model only, and clearing it to
//    "Same as bot" sends null;
//  - a cheaper pick on a high-stakes bot saves and shows the server's plain-words warning under the row;
//  - Task effort and Task context length beside it, "Same as bot" until set: each saves on its own and carries the
//    other parts forward (a model "" record is the bot's own model, shown as Same as bot); only all three Same as bot
//    sends null. The editor holds them until Save bot and sends one task-model save.
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
    const helper = {name: 'helper', display_name: 'Helper', description: '', status: 'active', owner_id: 'ana', repo: 'bot-helper',
      can_manage: true, users: [], bot_owners: [], revision: 4, model: 'claude-sonnet-5-5', harness: 'claude', reasoning_effort: 'high',
      keep_tasks_going: null, task_flow: {going: 2, waiting: 5}, task_model: null, thread_mode: 'personal', bot_contact: 'open', machine: {runner_id: 'r1', label: 'Box', owner_id: 'ana', generation: 7}};
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
          // As backend/settings_admin.py set_task_model answers: the stored record (task_models.stored: model '' with
          // only an effort or context length, null when nothing is set), and a warning for a cheaper pick.
          const t = body.task_model;
          if (t && t.harness && !t.model) return json({error: {code: 'harness', detail: 'Choose a task model to pick a harness for it'}}, 422);
          helper.task_model = !t || !(t.model || t.effort || t.compact_at) ? null
            : t.model ? {harness: t.harness, model: t.model, reasoning_effort: t.effort || 'high', runtime: t.harness, compact_at: t.compact_at || null}
            : {harness: '', model: '', reasoning_effort: t.effort || '', runtime: '', compact_at: t.compact_at || null};
          // The bot view carries the same warning, judged against the bot's model now.
          helper.task_model_warning = t?.model === 'claude-haiku-5-5' && helper.model === 'claude-sonnet-5-5' ? WARNING : '';
          return json({bot: 'helper', task_model: helper.task_model, revision: ++helper.revision, readiness: null,
            warning: helper.task_model_warning});
        }
        if (what === 'keep-tasks-going') {
          helper.keep_tasks_going = body.keep_tasks_going;
          return json({bot: 'helper', keep_tasks_going: body.keep_tasks_going, revision: ++helper.revision});
        }
        if (what === 'turns-at-once') {
          helper.turns_at_once = body.turns_at_once;
          return json({bot: 'helper', turns_at_once: body.turns_at_once, revision: ++helper.revision});
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
      if (p === '/api/v2/operations') return json({machines: [{id: 'r1', label: 'Box', owner_id: 'ana', bots: ['helper']}], services: []});
      if (p === '/api/v2/models') return json({...catalog, enabled_providers: ['openai', 'anthropic']});
      if (p === '/api/v2/providers') return json({enabled: ['openai', 'anthropic'], default: catalog.default, revision: 1,
        providers: [{id: 'openai', label: 'OpenAI'}, {id: 'anthropic', label: 'Anthropic'}]});
      if (p === '/api/v2/usage/limits') return json({bots: {}});
      if (p === '/api/v2/settings/history') return json({changes: [], transitions: []});
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      return json(unrouted(p));
    });
    const capture = async (name, target) => {
      if (!process.env.TICO_UI_RECEIPT_DIR) return;
      fs.mkdirSync(process.env.TICO_UI_RECEIPT_DIR, {recursive: true});
      await target.screenshot({path: require('node:path').join(process.env.TICO_UI_RECEIPT_DIR, name + '.png'), animations: 'disabled'});
    };
    const loaded = revision => page.waitForFunction(r => S.emps.find(e => e.name === 'helper')?.revision === r, revision);
    await page.goto('https://tico-ui.test/#/settings/devices');
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
    assert.deepEqual(calls[0].body, {task_model: {harness: 'codex', model: 'gpt-6-luna', effort: 'high', compact_at: null}, expected_revision: 4});
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
    assert.equal(await pickers.locator('[data-choice-effort]').isVisible(), false, 'Task effort stands in for the Effort picker');
    const effortPick = row.locator('[data-task-effort]'), contextPick = row.locator('[data-task-context]');
    assert.equal(await effortPick.inputValue(), 'high');
    assert.deepEqual(await contextPick.locator('option').allInnerTexts(), ['Same as bot', '150k', '250k', '400k', '600k', '1M'], 'the Compact at choices');
    // Same as bot for the model keeps the task effort: tasks still differ, so the mark stays on.
    calls = [];
    await pickers.locator('[data-choice-harness]').selectOption('');
    await pickers.locator('[data-choice-apply]').click();
    await loaded(7);
    assert.deepEqual(calls, [{what: 'task-model', body: {task_model: {model: null, effort: 'high', compact_at: null}, expected_revision: 6}}]);
    assert.equal(await row.count(), 1, 'the row stays open after Apply');
    assert.equal(await pickers.locator('[data-choice-harness] option:checked').innerText(), 'Same as bot', 'model "" shows Same as bot');
    assert.equal(await effortPick.inputValue(), 'high');
    assert.equal(await mark.evaluate(b => b.classList.contains('on')), true);
    // Only all three Same as bot clears it.
    calls = [];
    await effortPick.selectOption('');
    await loaded(8);
    assert.deepEqual(calls, [{what: 'task-model', body: {task_model: null, expected_revision: 7}}]);
    await page.waitForFunction(() => !document.querySelector('[data-task-open=helper]')?.classList.contains('on'));
    assert.equal(await page.evaluate(() => document.activeElement?.dataset.taskEffort), 'helper', 'the keyboard stays on Task effort');

    // 4/4. A cheaper pick on a high-stakes bot saves, and the row shows the warning in plain words.
    calls = [];
    await pickers.locator('[data-choice-harness]').selectOption('claude');
    await pickers.locator('[data-choice-model]').selectOption('claude-haiku-5-5');
    await pickers.locator('[data-choice-apply]').click();
    await loaded(9);
    assert.equal(calls[0].body.task_model.model, 'claude-haiku-5-5');
    await row.locator('[data-task-warning]').waitFor();
    assert.equal(await row.locator('[data-task-warning]').innerText(), WARNING);
    assert.equal(await mark.evaluate(b => b.classList.contains('on')), true, 'it saved');
    // Context only, then effort only: each carries the other parts. The mark names the context length.
    const savePart = async (select, value) => {
      const before = helper.revision; calls = [];
      await select.selectOption(value);
      await loaded(before + 1);
      assert.equal(calls.length, 1, 'one save per change');
      return calls[0].body.task_model;
    };
    assert.deepEqual(await savePart(contextPick, '600000'), {harness: 'claude', model: 'claude-haiku-5-5', effort: 'high', compact_at: 600000});
    assert.equal(await mark.innerText(), 'Tasks · 600k');
    await capture('desktop-task-row', page.locator('#settings-bots-list'));
    assert.deepEqual(await effortPick.locator('option').allInnerTexts(), ['low', 'high'], "the task model's efforts; no Same as bot while it runs them");
    assert.deepEqual(await savePart(effortPick, 'low'), {harness: 'claude', model: 'claude-haiku-5-5', effort: 'low', compact_at: 600000});
    // Same as bot for the model keeps the effort and context length; then each clears on its own.
    calls = []; let was = helper.revision;
    await pickers.locator('[data-choice-harness]').selectOption('');
    await pickers.locator('[data-choice-apply]').click();
    await loaded(was + 1);
    assert.deepEqual(calls.map(c => c.body.task_model), [{model: null, effort: 'low', compact_at: 600000}]);
    assert.equal(await pickers.locator('[data-choice-harness] option:checked').innerText(), 'Same as bot');
    assert.deepEqual(await effortPick.locator('option').allInnerTexts(), ['Same as bot', 'low', 'high'], "the bot's own model's efforts");
    assert.equal(await mark.innerText(), 'Tasks · 600k');
    assert.match(await mark.getAttribute('title'), /^Tasks run on the bot's model · low · 600k$/);
    assert.deepEqual(await savePart(effortPick, ''), {model: null, effort: null, compact_at: 600000});
    assert.equal(await mark.evaluate(b => b.classList.contains('on')), true, 'context only still differs');
    assert.equal(await savePart(contextPick, ''), null, 'all three Same as bot');
    assert.equal(await mark.innerText(), 'Tasks');
    assert.equal(await mark.evaluate(b => b.classList.contains('on')), false);

    // The editor: Task effort and Task context length are held until Save bot, then go in one save with the model.
    helper.task_model = {harness: 'claude', model: 'claude-haiku-5-5', reasoning_effort: 'high', runtime: 'claude', compact_at: null};
    helper.task_model_warning = WARNING; helper.revision++;
    await page.evaluate(() => loadSettings());
    await openEditor();
    assert.equal(await editor.locator('[data-task-warning]').innerText(), WARNING, 'the editor shows the warning under Task model');
    const editorEffort = editor.locator('[data-task-effort]'), editorContext = editor.locator('[data-task-context]');
    await editorContext.selectOption('1000000');
    await page.waitForTimeout(50);
    assert.deepEqual(calls, [], 'held until Save bot');
    assert.match(await editor.locator('[data-bot-pending]').innerText(), /task context length/);
    let before = helper.revision;
    await editor.locator('button[type=submit]').click();
    await loaded(before + 2);
    assert.deepEqual(calls.map(c => c.what), ['task-model', 'definition']);
    assert.deepEqual(calls[0].body.task_model, {harness: 'claude', model: 'claude-haiku-5-5', effort: 'high', compact_at: 1000000}, 'context saved, model and effort kept');
    // A new task model: Task effort lists its efforts and keeps the one held; one save carries all three.
    await openEditor();
    assert.equal(await editorContext.inputValue(), '1000000');
    await editorEffort.selectOption('low');
    await task.locator('[data-choice-harness]').selectOption('codex');
    await task.locator('[data-choice-model]').selectOption('gpt-6-luna');
    assert.equal(await editorEffort.inputValue(), 'low');
    before = helper.revision;
    await editor.locator('button[type=submit]').click();
    await loaded(before + 2);
    assert.deepEqual(calls.map(c => c.what), ['task-model', 'definition'], 'one task-model save');
    assert.deepEqual(calls[0].body.task_model, {harness: 'codex', model: 'gpt-6-luna', effort: 'low', compact_at: 1000000});
    // Same as bot for the model in the editor keeps the effort and context; it reopens as Same as bot.
    await openEditor();
    await task.locator('[data-choice-harness]').selectOption('');
    assert.deepEqual(await editorEffort.locator('option').allInnerTexts(), ['Same as bot', 'low', 'high']);
    before = helper.revision;
    await editor.locator('button[type=submit]').click();
    await loaded(before + 2);
    assert.deepEqual(calls[0].body.task_model, {model: null, effort: 'low', compact_at: 1000000});
    await openEditor();
    assert.equal(await task.locator('[data-choice-harness] option:checked').innerText(), 'Same as bot', 'model "" shows Same as bot');
    assert.equal(await editorEffort.inputValue(), 'low');
    assert.equal(await editorContext.inputValue(), '1000000');
    await editor.locator('[data-bot-choice][data-kind=task]').evaluate(n => n.scrollIntoView({block: 'center'}));
    await capture('desktop-task-editor', page);
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
    const parts = await row.locator('.sb-task-part').evaluateAll(nodes => nodes.map(n => { const r = n.getBoundingClientRect(); return [r.left, r.right]; }));
    assert.ok(parts.length === 2 && parts.every(([l, r]) => l >= 0 && r <= 390), `Task effort and Task context length fit the phone: ${JSON.stringify(parts)}`);
    await capture('phone-task-row', row);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'no sideways scroll');

    // Active-work caps save with Save bot at both widths, including off and the unlimited default.
    for (const width of [1440, 390]) {
      await page.setViewportSize({width, height: 900});
      await toBots();
      assert.match(await page.locator('[data-settings-bot=helper] [data-task-flow]').innerText(), /2 of unlimited tasks going, 5 waiting to start/);
      await openEditor();
      const keep = editor.locator('[data-bot-keep-tasks]');
      for (const [value, stored] of [['3', 3], ['0', 0], ['', null]]) {
        const before = helper.revision;
        calls = [];
        await keep.fill(value);
        await keep.dispatchEvent('change');
        await page.waitForTimeout(100);
        assert.deepEqual(calls, [], 'held until Save bot');
        await editor.locator('button[type=submit]').click();
        await loaded(before + 2);
        assert.deepEqual(calls.map(c => c.what), ['definition', 'keep-tasks-going']);
        assert.deepEqual(calls[1].body, {keep_tasks_going: stored, expected_revision: before + 1});
        assert.equal(helper.keep_tasks_going, stored);
        await openEditor();
      }
      // Turns at once saves the same way; a value out of range or not a whole number is named under its row
      // and not sent.
      const turns = editor.locator('[data-bot-turns-at-once]');
      for (const [value, stored] of [['5', 5], ['', null]]) {
        const before = helper.revision;
        calls = [];
        await turns.fill(value);
        await turns.dispatchEvent('change');
        await page.waitForTimeout(100);
        assert.deepEqual(calls, [], 'held until Save bot');
        await editor.locator('button[type=submit]').click();
        await loaded(before + 2);
        assert.deepEqual(calls.map(c => c.what), ['definition', 'turns-at-once']);
        assert.deepEqual(calls[1].body, {turns_at_once: stored, expected_revision: before + 1});
        assert.equal(helper.turns_at_once, stored);
        await openEditor();
      }
      for (const value of ['300', '2.5']) {
        calls = [];
        await turns.fill(value);
        await turns.dispatchEvent('change');
        await editor.locator('[data-field-err]').first().waitFor();
        await page.waitForTimeout(100);
        assert.deepEqual(calls, [], `${value} is not posted`);
        await turns.fill('');
        await turns.dispatchEvent('change');
        assert.equal(await editor.locator('[data-field-err]').count(), 0, 'the line clears once the value is valid');
        await openEditor();
      }
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      await page.evaluate(() => settingsEditBot('helper', true));
    }

    assert.deepEqual(errors, []);
    console.log('ok');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
