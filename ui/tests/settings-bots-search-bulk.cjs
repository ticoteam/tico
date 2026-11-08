// Spec v2 acceptance 4/5: model-aware search, visible match reasons, and bulk destinations.
// All state is synthetic, with the real bundled Settings UI and single-bot API contracts.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
(async () => {
  const browser = await chromium.launch({headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 1000}, serviceWorkers: 'block', reducedMotion: 'reduce'});
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.addInitScript(() => localStorage.setItem('hub.settings.bots.filters', JSON.stringify({computer: 'r2', model: 'claude::sonnet', effort: 'low'})));
    const opus = 'claude-opus-5-5', sonnet = 'claude-sonnet-5-5', haiku = 'claude-haiku-5-5';
    const model = id => ({id, label: id === opus ? 'Opus 5.5' : id === sonnet ? 'Sonnet 5.5' : 'Haiku 5.5', runtime: 'claude', provider: 'anthropic',
      efforts: ['low', 'high'], default_effort: 'high', harnesses: ['claude']});
    const choice = id => ({model: id, harness: 'claude', reasoning_effort: 'high'});
    const bot = (name, own, task) => ({name, display_name: name[0].toUpperCase() + name.slice(1), role: 'planner', team: 'research', description: 'Drafts forecasts',
      status: 'active', owner_id: 'ana', can_manage: true, users: [], bot_owners: [], revision: 4, ...choice(own), task_model: task ? choice(task) : null,
      machine: {runner_id: 'r1', label: 'Box', owner_id: 'ana', generation: 7}});
    // `inherit` runs its tasks on its own model with a task context length (a model "" record): search reads it as no task model.
    const bots = [bot('alpha', opus, sonnet), bot('beta', sonnet, opus), bot('gamma', opus, opus), bot('delta', sonnet, null),
      {...bot('inherit', opus, null), task_model: {harness: '', model: '', reasoning_effort: '', runtime: '', compact_at: 600000}},
      {...bot('default', '', null), resolved_model: opus, resolved_runtime: 'claude'}];
    // The real UI must keep read-only results visible without selecting them.
    bots.push({...bot('readable', sonnet, opus), can_manage: false, owner_id: 'other'});
    let calls = [], inflight = 0, maxInflight = 0, conflict = '', fail = '';
    await page.route('**/*', async route => {
      const req = route.request(), p = new URL(req.url()).pathname;
      const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
      if (ui && fs.existsSync(uiFile(ui[1]))) return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
      if (p === '/vendor/fonts/material-symbols-outlined.woff2') return route.fulfill({contentType: 'font/woff2', body: fs.readFileSync(uiFile('vendor/fonts/material-symbols-outlined.woff2'))});
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      const change = p.match(/^\/api\/v2\/bots\/([^/]+)\/(task-model|transitions)$/);
      if (req.method() === 'POST' && change) {
        const [, slug, what] = change, e = bots.find(b => b.name === slug), body = req.postDataJSON();
        calls.push({slug, what, body}); inflight++; maxInflight = Math.max(maxInflight, inflight);
        try {
          await new Promise(resolve => setTimeout(resolve, 15));
          if (`${slug}/${what}` === conflict) { conflict = ''; e.revision++; return json({error: {code: 'version_conflict', detail: 'Settings changed'}}, 409); }
          if (body.expected_revision !== e.revision) return json({error: {code: 'version_conflict', detail: 'Wrong revision'}}, 409);
          if (`${slug}/${what}` === fail) { fail = ''; return json({error: {code: 'test_failure', detail: 'Synthetic task save failure'}}, 422); }
          if (what === 'transitions') { Object.assign(e, choice(body.model), {reasoning_effort: body.effort}); e.revision++; return json({state: 'applied'}); }
          e.task_model = {...choice(body.task_model.model), reasoning_effort: body.task_model.effort, compact_at: body.task_model.compact_at ?? null}; e.revision++;
          return json({revision: e.revision, task_model: e.task_model, readiness: {can_run: false, problem: 'Provider is signed out'}, warning: 'This bot sends messages. Check task results carefully.'});
        } finally { inflight--; }
      }
      if (p === '/api/me') return json({id: 'ana', role: 'member', name: 'Ana', email: 'ana@example.com', cloud: true, registered: true});
      if (p === '/api/v2/config') return json({version: '0.1.0', app_name: 'Tico', local: true});
      if (p === '/api/employees') return json(bots);
      if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana'}], teams: {research: {label: 'Research'}}});
      if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: []});
      if (p === '/api/v2/status') return json({bots: []});
      if (p === '/api/v2/operations') return json({machines: [{id: 'r1', label: 'Box', owner_id: 'ana', bots: bots.map(b => b.name)}], services: [],
        runnable: {beta: {claude: {can_run: false, problem: 'Provider is signed out', short: 'sign in'}}}});
      if (p === '/api/v2/models') return json({models: [opus, sonnet, haiku].map(model), harnesses: [{id: 'claude', label: 'Claude Code', runtime: 'claude'}], enabled_providers: ['anthropic']});
      if (p === '/api/v2/providers') return json({enabled: ['anthropic'], revision: 1, providers: [{id: 'anthropic', label: 'Anthropic'}]});
      if (p === '/api/v2/usage/limits') return json({bots: {}});
      if (p === '/api/v2/settings/history') return json({changes: [], transitions: []});
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      return json(unrouted(p));
    });
    await page.goto('https://tico-ui.test/#/settings');
    await page.waitForFunction(() => typeof S !== 'undefined' && S.emps?.length === 7 && SETTINGS_DATA.models?.length);
    await page.evaluate(() => settingsShow('bots'));
    const search = page.locator('[data-bots-search]'), rows = page.locator('[data-settings-bot]');
    assert.equal(await rows.count(), 7, 'old computer/model/effort preferences cannot hide bots');
    assert.equal(await page.evaluate(() => localStorage.getItem('hub.settings.bots.filters')), null, 'retired preference removed');
    assert.equal(await page.locator('[data-bots-filter],[data-bots-filters-toggle]').count(), 0);
    const shown = () => rows.evaluateAll(nodes => nodes.map(n => n.dataset.settingsBot).sort());
    await search.pressSequentially('OPUS');
    assert.deepEqual(await shown(), ['alpha', 'beta', 'default', 'gamma', 'inherit', 'readable']);
    const reason = slug => page.locator(`[data-settings-bot=${slug}] [data-model-match]`).innerText();
    assert.equal(await reason('alpha'), 'Bot model: Opus 5.5');
    assert.equal(await reason('beta'), 'Task model: Opus 5.5');
    for (const slug of ['gamma', 'inherit', 'default']) assert.equal(await reason(slug), 'Bot model: Opus 5.5 · Task model: Opus 5.5');
    const mark = slug => page.locator(`[data-settings-bot=${slug}] [data-task-open]`);
    assert.equal(await mark('inherit').innerText(), 'Tasks · 600k', 'model "" with a context length: tasks differ, no task model');
    assert.equal(await mark('inherit').evaluate(b => b.classList.contains('on')), true);
    await page.evaluate(() => loadSettings());
    assert.equal(await search.inputValue(), 'OPUS');
    assert.equal(await search.evaluate(e => e === document.activeElement), true, 'refresh preserves typing focus');
    const capture = async name => {
      if (!process.env.TICO_UI_RECEIPT_DIR) return;
      fs.mkdirSync(process.env.TICO_UI_RECEIPT_DIR, {recursive: true});
      await page.screenshot({path: path.join(process.env.TICO_UI_RECEIPT_DIR, name + '.png'), fullPage: true, animations: 'disabled'});
    };
    await capture('desktop-search');
    // Keep each existing search field and allow words to span metadata and model names.
    for (const q of ['Alpha', 'planner', 'forecasts', 'research', 'planner OPUS', 'claude-opus-5-5']) {
      await search.fill(q); assert.ok((await shown()).includes('alpha'), `search includes ${q}`);
    }
    await search.fill('opus');
    await page.locator('[data-bots-select-all]').check();
    assert.deepEqual(await page.evaluate(() => [...SETTINGS_BOTS_VIEW.selected].sort()), ['alpha', 'beta', 'default', 'gamma', 'inherit']);
    assert.equal(await page.locator('[data-settings-bot=readable] input[type=checkbox]').isDisabled(), true);
    await search.fill('beta');
    assert.deepEqual(await page.evaluate(() => [...SETTINGS_BOTS_VIEW.selected]), ['beta'], 'hidden selections are dropped');
    await search.fill('opus'); await page.locator('[data-bots-select-all]').check();
    const selected = ['alpha', 'beta', 'default', 'gamma', 'inherit'];
    const ownBefore = bots.map(b => [b.name, b.model, b.harness, b.reasoning_effort]);
    const dialog = page.locator('.settings-bulk-dialog');
    const open = async (target, id) => {
      await page.locator('[data-bulk-model]').click();
      await dialog.locator('[data-bulk-target]').selectOption(target);
      await dialog.locator('[data-choice-harness]').selectOption('claude');
      await dialog.locator('[data-choice-model]').selectOption(id);
    };
    const apply = async () => {
      await dialog.locator('[data-bulk-apply]').click();
      await dialog.locator('[data-bulk-close]', {hasText: 'Done'}).waitFor();
      await page.waitForFunction(() => document.querySelector('.settings-bulk-dialog [data-bulk-summary]')?.textContent);
    };
    const close = async () => {
      await dialog.locator('[data-bulk-close]', {hasText: 'Done'}).click();
      await page.waitForFunction(() => !document.querySelector('.settings-bulk-dialog') && SETTINGS_BOTS_VIEW.selected.size === 0);
      await page.evaluate(() => loadSettings());
    };
    calls = []; await open('task', haiku);
    assert.match(await dialog.locator('[data-bulk-explanation]').innerText(), /Only task models change/);
    await capture('desktop-task-dialog');
    await apply();
    assert.deepEqual(calls.map(c => c.slug).sort(), selected, 'one task-model write per matching, manageable bot');
    assert.ok(calls.every(c => c.what === 'task-model' && c.body.task_model.model === haiku && c.body.task_model.effort === 'high' && c.body.expected_revision === 4));
    assert.deepEqual(Object.fromEntries(calls.map(c => [c.slug, c.body.task_model.compact_at])),
      {alpha: null, beta: null, default: null, gamma: null, inherit: 600000}, "each bot keeps its own task context length");
    assert.equal(bots.find(b => b.name === 'inherit').task_model.compact_at, 600000);
    assert.deepEqual(bots.map(b => [b.name, b.model, b.harness, b.reasoning_effort]), ownBefore, 'task-only never changes bot configuration');
    assert.equal(bots.find(b => b.name === 'delta').task_model, null, 'nonmatching row unchanged');
    assert.equal(bots.find(b => b.name === 'readable').task_model.model, opus, 'read-only row unchanged');
    assert.match(await dialog.locator('[data-bulk-bot=beta]').innerText(), /For now tasks use the bot model: Provider is signed out/);
    assert.match(await dialog.locator('[data-bulk-bot=beta]').innerText(), /This bot sends messages/);
    assert.equal(maxInflight, 1, 'single-bot requests run sequentially');
    await close();
    // Bot-only keeps an explicit task override, then Both consumes the bot transition's newer revision.
    await search.fill('alpha'); await page.locator('[data-bots-select-all]').check();
    calls = []; await open('bot', sonnet); await apply();
    assert.deepEqual(calls.map(c => c.what), ['transitions']);
    assert.equal(bots[0].task_model.model, haiku); await close();
    await search.fill('alpha'); await page.locator('[data-bots-select-all]').check();
    calls = []; conflict = 'alpha/task-model'; fail = 'alpha/task-model';
    await open('both', opus); await apply();
    assert.match(await dialog.locator('[data-bulk-summary]').innerText(), /1 failed/);
    assert.equal(bots[0].model, opus); assert.equal(bots[0].task_model.model, haiku);
    assert.deepEqual(calls.map(c => c.what), ['transitions', 'task-model', 'task-model']);
    assert.deepEqual(calls.map(c => c.body.expected_revision), [6, 7, 8], 'Both and stale-revision retry use the current revision');
    await dialog.locator('[data-bulk-retry]').click();
    await page.waitForFunction(() => document.querySelector('.settings-bulk-dialog [data-bulk-summary]')?.textContent === '1 changed');
    assert.deepEqual(calls.map(c => c.what), ['transitions', 'task-model', 'task-model', 'task-model'], 'partial retry never repeats an applied bot transition');
    assert.equal(bots[0].task_model.model, opus); await close();
    // Both must still save task settings when the bot destination is blocked or fails.
    await search.fill('beta'); await page.locator('[data-bots-select-all]').check();
    calls = []; await open('both', sonnet); await apply();
    assert.deepEqual(calls.map(c => c.what), ['task-model'], 'blocked bot destination never suppresses a task save');
    assert.equal(bots[1].model, sonnet); assert.equal(bots[1].task_model.model, sonnet);
    assert.match(await dialog.locator('[data-bulk-summary]').innerText(), /1 partly changed/);
    assert.match(await dialog.locator('[data-bulk-bot=beta]').innerText(), /Bot model: Can't run/);
    assert.match(await dialog.locator('[data-bulk-bot=beta]').innerText(), /Task model: Now Claude Sonnet/);
    await capture('desktop-both-blocked'); await close();
    await search.fill('gamma'); await page.locator('[data-bots-select-all]').check();
    calls = []; fail = 'gamma/transitions'; await open('both', sonnet); await apply();
    assert.deepEqual(calls.map(c => c.what), ['transitions', 'task-model'], 'failed bot destination never suppresses a task save');
    assert.equal(bots[2].model, opus); assert.equal(bots[2].task_model.model, sonnet);
    assert.match(await dialog.locator('[data-bulk-summary]').innerText(), /1 failed/);
    assert.match(await dialog.locator('[data-bulk-summary]').innerText(), /1 partly changed/);
    assert.match(await dialog.locator('[data-bulk-bot=gamma]').innerText(), /Bot model: Synthetic task save failure/);
    assert.match(await dialog.locator('[data-bulk-bot=gamma]').innerText(), /Task model: Now Claude Sonnet/);
    await capture('desktop-both-failed');
    await dialog.locator('[data-bulk-retry]').click();
    await page.waitForFunction(() => document.querySelector('.settings-bulk-dialog [data-bulk-summary]')?.textContent === '1 changed');
    assert.deepEqual(calls.map(c => c.what), ['transitions', 'task-model', 'transitions'], 'retry preserves the task destination already saved');
    assert.equal(bots[2].model, sonnet); await close();
    // Restore this row through the same UI so the phone search retains its original coverage.
    await search.fill('gamma'); await page.locator('[data-bots-select-all]').check();
    await open('both', opus); await apply();
    assert.equal(bots[2].model, opus); assert.equal(bots[2].task_model.model, opus); await close();
    // Phone: one search, matching-row select all, the Task destination, wrapping, and keyboard clear.
    await page.setViewportSize({width: 390, height: 844});
    await search.fill('opus'); await page.locator('[data-bots-picking]').click();
    await page.locator('[data-bots-pick-all]').click();
    assert.deepEqual(await page.evaluate(() => [...SETTINGS_BOTS_VIEW.selected].sort()), ['alpha', 'default', 'gamma', 'inherit']);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'phone search fits');
    const geometry = await page.locator('[data-model-match]').evaluateAll(nodes => nodes.map(n => {
      const r = n.getBoundingClientRect(); return {width: r.width, height: r.height, left: r.left, right: r.right};
    }));
    assert.ok(geometry.every(r => r.width >= 150 && r.height < 60 && r.left >= 0 && r.right <= 390), `phone model reasons stay readable: ${JSON.stringify(geometry)}`);
    await capture('phone-search'); await open('task', sonnet);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'phone dialog fits');
    assert.equal(await dialog.locator('[data-bulk-target]').isVisible(), true);
    await capture('phone-task-dialog'); calls = []; await apply();
    assert.deepEqual(calls.map(c => c.slug).sort(), ['alpha', 'default', 'gamma', 'inherit']);
    assert.ok(calls.every(c => c.what === 'task-model')); await close();
    await search.press('Escape'); assert.equal(await search.inputValue(), ''); assert.equal(await rows.count(), 7);
    assert.deepEqual(errors, []);
    console.log('ok: search reasons, inherited/default model, metadata, retired state, selection, all destinations, sequential saves, warnings, conflict/partial retry, desktop + phone');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
