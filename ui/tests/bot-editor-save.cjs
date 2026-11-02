// Bot editor Save never drops a Model or Fallback picked but not Applied (TK-BLOG3EDS, Tico 0.3.54):
//  - a Fallback pick goes to POST /v2/bots/{bot}/fallback (the body Apply sends, with expected_revision) before the
//    definition, and reopening shows it;
//  - a Model pick opens its transition dialog; Cancel keeps the editor open with a line and saves nothing; Prepare
//    change saves the definition, then starts the model transition from the new revision;
//  - a refused /fallback keeps the editor open with the error and saves nothing else;
//  - when the definition is refused after the fallback saved, Save again sends only the definition;
//  - Model and Fallback both pending: Fallback, definition, then the transition; a refused transition keeps the editor
//    open with the pick, and Save again sends the definition and the transition, not the Fallback;
//  - a half-made pick (harness, no model) keeps the editor open and says so;
//  - with nothing pending, Save posts the definition only, as before;
//  - every other row waits for Save bot too: a model, compact-at, keep-tasks and turns-at-once change posts nothing
//    until Save bot, which then saves all of them, each at the revision the write before it made;
//  - Archive bot asks first, naming who gets its work, and Cancel archives nothing;
//  - Slug renames a bot whose id is not its slug: the new slug goes with the definition, a taken one shows the
//    server's refusal, the bot keeps its id; a built-in bot's slug cannot be changed.
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
      models: [model('gpt-6-luna', 'codex', 'openai'), model('gpt-6-1-sol', 'codex', 'openai'), model('opus-5-5', 'claude', 'anthropic')],
      harnesses: [{id: 'codex', label: 'Codex (OpenAI)', runtime: 'codex'}, {id: 'claude', label: 'Claude Code (Anthropic)', runtime: 'claude', capabilities: {compaction: true}}],
      default: {runtime: 'claude', model: 'opus-5-5'},
    };
    const helper = {name: 'helper', display_name: 'Helper', description: '', status: 'active', owner_id: 'ana', repo: 'bot-helper',
      can_manage: true, users: [], bot_owners: [], revision: 4, model: 'opus-5-5', harness: 'claude', reasoning_effort: 'high',
      thread_mode: 'personal', bot_contact: 'open', machine: {runner_id: 'r1', label: 'Box', owner_id: 'ana', generation: 7}};
    const bots = [helper];
    const SCOUT_ID = '01990000-0000-7000-8000-00000000a11c';
    const scout = {id: SCOUT_ID, slug: 'scout', name: 'scout', display_name: 'Scout', description: '', status: 'active', owner_id: 'ana',
      repo: 'bot-scout', can_manage: true, users: [], bot_owners: [], revision: 1, model: 'opus-5-5', harness: 'claude',
      reasoning_effort: 'high', thread_mode: 'personal', bot_contact: 'open'};
    const botops = {...scout, id: 'botops', slug: 'botops', name: 'botops', display_name: 'BotOps', repo: 'bot-botops'};
    const renames = [];
    let calls = [], refuseFallback = false, refuseDefinition = false, refuseTransition = false;
    await page.route('**/*', route => {
      const req = route.request(), p = new URL(req.url()).pathname;
      const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
      if (ui && fs.existsSync(uiFile(ui[1])))
        return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      if (req.method() === 'POST' && p === `/api/v2/bots/${SCOUT_ID}/definition`) {
        const body = req.postDataJSON();
        renames.push(body);
        if (body.slug === 'helper') return json({error: {code: 'duplicate', detail: 'Another bot already uses helper'}}, 409);
        Object.assign(scout, body.slug ? {slug: body.slug, name: body.slug} : {}, {display_name: body.display_name, revision: scout.revision + 1});
        return json({id: SCOUT_ID, slug: scout.slug, display_name: scout.display_name, revision: scout.revision});
      }
      if (req.method() === 'POST' && p.startsWith('/api/v2/bots/helper/')) {
        const body = req.postDataJSON(), what = p.split('/').pop();
        calls.push({what, body});
        // Each write checks and bumps the revision, as backend/settings_admin.py does.
        if (body.expected_revision !== helper.revision) return json({error: {detail: 'Bot configuration changed; refresh before saving'}}, 409);
        if (what === 'fallback') {
          if (refuseFallback) return json({error: {detail: 'Choose one of the supported models'}}, 422);
          if (body.fallback) helper.fallback = {harness: body.fallback.harness, model: body.fallback.model, reasoning_effort: body.fallback.effort, runtime: body.fallback.harness};
          else delete helper.fallback;
          return json({bot: 'helper', fallback: helper.fallback || null, revision: ++helper.revision});
        }
        if (what === 'definition') {
          if (refuseDefinition) return json({error: {detail: 'Name is taken'}}, 422);
          Object.assign(helper, {display_name: body.display_name, description: body.description});
          return json({name: 'helper', display_name: helper.display_name, revision: ++helper.revision});
        }
        if (what === 'compact-at' || what === 'keep-tasks-going' || what === 'turns-at-once') {
          return json({bot: 'helper', revision: ++helper.revision});
        }
        if (what === 'archive') return json({bot: 'helper', archived: true});
        if (what === 'transitions' && refuseTransition) return json({error: {detail: 'Bot configuration changed; refresh before saving'}}, 409);
        if (what === 'transitions') return json({id: 'tr-1', bot: 'helper', kind: 'model', state: 'preparing'});
      }
      if (p === '/api/v2/settings/transitions/tr-1') return json({id: 'tr-1', bot: 'helper', kind: 'model', state: 'preparing',
        progress: {prepared: 0, total: 1}, target: {harness: 'codex', model: 'gpt-6-1-sol', effort: 'high'}});
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
    const editor = page.locator('#bot-editor');
    const row = kind => editor.locator(`[data-bot-choice][data-kind=${kind}]`);
    const open = async () => {
      // After a Save the page reloads settings; open on the bot as the server now has it.
      await page.waitForFunction(revision => S.emps.find(e => e.name === 'helper')?.revision === revision, helper.revision);
      await page.evaluate(() => settingsEditBot('helper'));
      await page.locator('#pane-settings').waitFor();
      await page.waitForFunction(() => !!document.querySelector('#bot-editor [data-bot-status]'));
      await page.evaluate(() => settingsEditBot('helper', true));
      await row('fallback').locator('[data-choice-harness]').waitFor();
      calls = [];
    };
    const pick = async (kind, harness, id) => {
      await row(kind).locator('[data-choice-harness]').selectOption(harness);
      if (id) await row(kind).locator('[data-choice-model]').selectOption(id);
    };
    const closed = () => page.waitForFunction(() => document.querySelector('#bot-editor [data-bot-status]')?.textContent === 'Saved').catch(async error => {
      throw new Error(`the editor stayed open: ${await editor.locator('[data-bot-status]').innerText().catch(() => '')} (${JSON.stringify(calls)})`, {cause: error}); });
    const statusText = () => editor.locator('[data-bot-status]').innerText();
    const save = () => editor.locator('button[type=submit]').click();
    await page.goto('https://tico-ui.test/#/settings');
    await page.waitForFunction(() => typeof S !== 'undefined' && S.emps?.some(e => e.name === 'helper') && SETTINGS_DATA.models?.length);

    // 4. Nothing pending: the definition only, at the revision it opened with.
    await open();
    await save();
    await closed();
    assert.deepEqual(calls.map(c => c.what), ['definition'], 'Save with no pending pick posts the definition only');
    assert.equal(calls[0].body.expected_revision, 4);

    // 1. A Fallback picked but not Applied: Save sends it (same body as Apply) first, then the definition.
    await open();
    await pick('fallback', 'codex', 'gpt-6-1-sol');
    assert.equal(await row('fallback').locator('[data-choice-apply]').count(), 0, 'no row saves on its own: there is no Apply');
    await save();
    await closed();
    assert.deepEqual(calls.map(c => c.what), ['fallback', 'definition'], 'Save applies the pending fallback, then the definition');
    assert.deepEqual(calls[0].body, {fallback: {harness: 'codex', model: 'gpt-6-1-sol', effort: 'high'}, expected_revision: 5});
    assert.equal(calls[1].body.expected_revision, 6, 'the definition follows the revision the fallback made');
    await page.waitForFunction(() => S.emps.find(e => e.name === 'helper')?.fallback?.model === 'gpt-6-1-sol');
    await open();
    assert.equal(await row('fallback').locator('[data-choice-model]').inputValue(), 'gpt-6-1-sol', 'reopening shows the new fallback');
    assert.equal(await row('fallback').locator('[data-choice-harness]').inputValue(), 'codex');

    // 3. A refused /fallback keeps the editor open with the error, and the definition is not sent.
    refuseFallback = true;
    await pick('fallback', '');   // None
    await save();
    await editor.locator('[data-bot-status] .err').waitFor();
    assert.match(await statusText(), /Choose one of the supported models/, 'the refusal is shown in the editor');
    assert.equal(await editor.isVisible(), true, 'the editor stays open');
    assert.deepEqual(calls.map(c => c.what), ['fallback'], 'nothing else is saved');
    assert.equal(calls[0].body.fallback, null, 'None is sent as no fallback');
    assert.equal(await editor.locator('button[type=submit]').isEnabled(), true, 'Save can be pressed again');
    refuseFallback = false;
    await page.evaluate(() => settingsEditBot('helper', true));

    // The fallback saves but the definition is refused: the editor stays open, and Save again does not resend it.
    await open();
    await pick('fallback', 'codex', 'gpt-6-luna');
    refuseDefinition = true;
    await save();
    await editor.locator('[data-bot-status] .err').waitFor();
    assert.match(await statusText(), /Name is taken/);
    assert.deepEqual(calls.map(c => c.what), ['fallback', 'definition']);
    refuseDefinition = false; calls = [];
    await save();
    await closed();
    assert.deepEqual(calls.map(c => c.what), ['definition'], 'the saved fallback is not sent twice');
    assert.equal(calls[0].body.expected_revision, 8, 'at the revision the fallback made');

    // A half-made pick (harness, no model) is not dropped either.
    await open();
    await pick('fallback', 'claude');
    await save();
    await editor.locator('[data-bot-status] .err').waitFor();
    assert.match(await statusText(), /Nothing saved: finish choosing the fallback/);
    assert.deepEqual(calls, [], 'nothing is saved');
    await page.evaluate(() => settingsEditBot('helper', true));

    // 2. A Model picked but not Applied: Save opens its transition dialog. Cancel saves nothing and says why.
    await open();
    await pick('model', 'codex', 'gpt-6-1-sol');
    await save();
    const transition = page.locator('#transition-dialog');
    await transition.locator('[data-confirm-prepare]').waitFor();
    assert.match(await transition.innerText(), /Change model settings · Helper/);
    assert.deepEqual(calls, [], 'nothing is saved before the model change is confirmed');
    await transition.locator('[data-confirm-cancel]').last().click();
    await editor.locator('[data-bot-status] .err').waitFor();
    assert.match(await statusText(), /Nothing saved: the model change was not confirmed/);
    assert.equal(await editor.isVisible(), true, 'no definition-only close');
    assert.deepEqual(calls, [], 'Cancel saves nothing');
    assert.equal(await row('model').locator('[data-choice-model]').inputValue(), 'gpt-6-1-sol', 'the pick is still there');
    // Prepare change: the definition, then the model transition from the revision the definition made.
    await save();
    await transition.locator('[data-confirm-prepare]').click();
    await closed();
    assert.deepEqual(calls.map(c => c.what), ['definition', 'transitions']);
    assert.deepEqual(calls[1].body, {kind: 'model', model: 'gpt-6-1-sol', effort: 'high', harness: 'codex',
      expected_generation: 7, expected_revision: 10});
    await page.waitForFunction(() => /Checkpointing 0 of 1/.test(document.querySelector('#transition-dialog')?.innerText || ''));
    await page.evaluate(() => { clearInterval(SETTINGS_TRANSITION_TIMER); document.querySelector('#transition-dialog').close(); });

    // Model and Fallback both pending, and the transition is refused after the Fallback and the definition saved.
    await open();
    await pick('fallback', 'codex', 'gpt-6-1-sol');
    await pick('model', 'codex', 'gpt-6-1-sol');
    refuseTransition = true;
    await save();
    await transition.locator('[data-confirm-prepare]').click();
    await editor.locator('[data-bot-status] .err').waitFor();
    assert.match(await statusText(), /Bot configuration changed/, 'the refused transition is shown in the editor');
    assert.deepEqual(calls.map(c => [c.what, c.body.expected_revision]), [['fallback', 10], ['definition', 11], ['transitions', 12]],
      'Fallback, then the definition, then the transition, each at the revision the write before it made');
    assert.equal(await row('model').locator('[data-choice-model]').inputValue(), 'gpt-6-1-sol', 'the Model pick is kept');
    refuseTransition = false; calls = [];
    await save();
    await transition.locator('[data-confirm-prepare]').click();
    await closed();
    assert.deepEqual(calls.map(c => [c.what, c.body.expected_revision]), [['definition', 12], ['transitions', 13]],
      'Save again does not resend the saved Fallback');
    await page.waitForFunction(() => /Checkpointing 0 of 1/.test(document.querySelector('#transition-dialog')?.innerText || ''));
    await page.evaluate(() => { clearInterval(SETTINGS_TRANSITION_TIMER); document.querySelector('#transition-dialog').close(); });

    // Model, compact-at, keep-tasks and turns at once changed: nothing is posted until Save bot, then all
    // of them save together.
    await open();
    await pick('model', 'codex', 'gpt-6-luna');
    await editor.locator('[data-bot-compact]').selectOption('400000');
    await editor.locator('[data-bot-keep-tasks]').fill('3');
    await editor.locator('[data-bot-keep-tasks]').dispatchEvent('change');
    await editor.locator('[data-bot-turns-at-once]').fill('5');
    await editor.locator('[data-bot-turns-at-once]').dispatchEvent('change');
    await editor.locator('[data-bot-pending]:not([hidden])').waitFor();
    await page.waitForTimeout(200);
    assert.deepEqual(calls, [], 'nothing saves before Save bot');
    await save();
    await transition.locator('[data-confirm-prepare]').click();
    await closed();
    assert.deepEqual(calls.map(c => [c.what, c.body.expected_revision]),
      [['definition', 13], ['compact-at', 14], ['keep-tasks-going', 15], ['turns-at-once', 16], ['transitions', 17]],
      'Save bot saves every held change, chaining the revision');
    assert.equal(calls[1].body.compact_at, 400000);
    assert.equal(calls[2].body.keep_tasks_going, 3);
    assert.deepEqual(calls[3].body, {turns_at_once: 5, expected_revision: 16});
    await page.waitForFunction(() => /Checkpointing 0 of 1/.test(document.querySelector('#transition-dialog')?.innerText || ''));
    await page.evaluate(() => { clearInterval(SETTINGS_TRANSITION_TIMER); document.querySelector('#transition-dialog').close(); });

    // Archive bot asks first and names who gets the work; Cancel archives nothing and leaves the editor open.
    await open();
    await editor.locator('[data-bot-remove]').click();
    const ask = page.locator('dialog.settings-confirm');
    await ask.waitFor();
    assert.match(await ask.innerText(), /Ana/, 'the confirm names who gets its work');
    await ask.locator('[data-confirm-cancel]').click();
    await ask.waitFor({state: 'detached'});
    await page.waitForTimeout(200);
    assert.deepEqual(calls, [], 'Cancel archives nothing');
    assert.equal(await editor.isVisible(), true);
    await editor.locator('[data-bot-remove]').click();
    await ask.locator('[data-confirm-ok]').click();
    for (let i = 0; i < 50 && !calls.length; i++) await page.waitForTimeout(100);
    assert.deepEqual(calls.map(c => c.what), ['archive'], 'Archive bot archives once confirmed');

    // Slug: a bot whose id is not its slug is renamed with its definition and keeps its id.
    bots.push(scout, botops);
    await page.evaluate(() => loadSettings());
    await page.waitForFunction(id => S.emps.some(e => e.id === id), SCOUT_ID);
    await page.evaluate(id => settingsEditBot(id), SCOUT_ID);
    await page.waitForFunction(id => location.hash === `#/bot/${id}/settings`, SCOUT_ID);
    const slugField = editor.locator('input[name=slug]');
    const editScout = async () => {
      await page.waitForFunction(() => !!document.querySelector('#bot-editor [data-bot-status]'));
      await page.evaluate(id => settingsEditBot(id, true), SCOUT_ID);
      await row('fallback').locator('[data-choice-harness]').waitFor();
      await slugField.waitFor({state: 'attached'});
      await editor.locator('[data-bot-advanced]').evaluate(el => { el.open = true; });
    };
    await editScout();
    assert.equal(await slugField.inputValue(), 'scout');
    assert.equal(await slugField.getAttribute('readonly'), null);
    await slugField.fill('helper');
    await save();
    await editor.locator('[data-bot-status] .err').waitFor();
    assert.match(await statusText(), /Another bot already uses helper/, 'a taken slug shows the server refusal');
    assert.equal(renames.at(-1).slug, 'helper');
    await slugField.fill('scout-2');
    await save();
    await closed();
    assert.equal(renames.at(-1).slug, 'scout-2');
    await page.waitForFunction(id => S.emps.find(e => e.id === id)?.slug === 'scout-2', SCOUT_ID);
    assert.equal(await page.evaluate(() => location.hash), `#/bot/${SCOUT_ID}/settings`, 'the page keeps its id address');
    // Saving with the slug unchanged sends no slug.
    await editScout();
    await save();
    await closed();
    assert.equal('slug' in renames.at(-1), false);
    // A built-in bot's slug is fixed.
    await page.evaluate(() => settingsEditBot('botops'));
    await page.waitForFunction(() => location.hash === '#/bot/botops/settings');
    await page.waitForFunction(() => !!document.querySelector('#bot-editor [data-bot-status]'));
    await page.evaluate(() => settingsEditBot('botops', true));
    await row('fallback').locator('[data-choice-harness]').waitFor();
    assert.equal(await slugField.getAttribute('readonly'), '');
    assert.deepEqual(errors, []);
    console.log('ok');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
