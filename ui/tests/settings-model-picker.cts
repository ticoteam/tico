// The one model picker in Settings > Bots (ui/app/settings-bots-choice.ts, task #236):
//  - closed it reads model, effort and window ("Opus 5.5 MED 400K"): no harness, dots or "(default)"; no window for a
//    harness that does not compact; a task model on Same as bot reads "Same as bot", or "Same as bot 150K";
//  - open it is one panel: Harness, Model (each with its window), Effort and a Compact select listing only the sizes
//    the model can hold, the model's default marked; a lowered size and a default that follows the model say so
//    before Apply;
//  - each place saves its own: the bots table (the bot's window, the task window), the bot editor, new bot and bulk
//    change; a window alone never starts a model change;
//  - arrows move within a row and Enter applies; 390px wide without sideways scroll; read-only for a person who may
//    not manage the bot.
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cts') as import('./support/page.cts').Api;
const {unrouted} = require('./support/routes.cts') as import('./support/routes.cts').Api;
const {openPicker, choose, chosen, offered, applyPicker, closedText} = require('./support/model-picker.cts') as import('./support/model-picker.cts').Api;
type Page = import('playwright').Page;
type TaskModel = {harness: string; model: string; reasoning_effort: string; runtime: string; compact_at: number | null};
type Bot = {id: string; name: string; slug: string; display_name: string; description: string; status: string; owner_id: string; repo: string;
  can_manage: boolean; users: unknown[]; bot_owners: unknown[]; revision: number; model: string; harness: string; runtime: string;
  reasoning_effort: string; compact_at?: number; task_model: TaskModel | null; thread_mode: string; bot_contact: string;
  machine: {runner_id: string; label: string; owner_id: string; generation: number}};
type Body = Record<string, unknown> & {expected_revision?: number; compact_at?: number; task_model?: Record<string, unknown> | null};
declare const S: {emps: {name: string; revision: number}[]; route: string};
declare var SETTINGS: string;
declare var SETTINGS_DATA: {models: unknown[]};
declare var SETTINGS_BOTS_VIEW: {selected: Set<string>};
declare function settingsShow(tab: string): void;
declare function settingsEditBot(slug?: string, inline?: boolean): void;
declare function settingsBulkModelDialog(): void;
declare var renderSettingsBots: () => void;
declare function loadSettings(): Promise<void>;

const SIZES = ['100K', '150K', '200K', '250K', '300K', '400K', '500K', '600K', '700K', '800K', '900K', '1M'];
(async () => {
  const browser = await chromium.launch({headless: true});
  try {
    const model = (id: string, harnesses: string[], provider: string, context: number, compact: number) => ({id, label: id.replace(/^claude-/, '').replace(/-(\d)-(\d)/, ' $1.$2').replace(/-/g, ' '),
      runtime: harnesses[0], harnesses, provider, efforts: ['low', 'medium', 'high', 'xhigh', 'max'], default_effort: 'medium', context_tokens: context, compact_default: compact});
    const catalog = {
      models: [model('claude-opus-5-5', ['claude'], 'anthropic', 1_000_000, 400_000), model('claude-haiku-5-5', ['claude'], 'anthropic', 1_000_000, 150_000),
        model('gpt-5-6-sol', ['codex'], 'openai', 500_000, 250_000), model('gpt-6-sol', ['codex', 'nocomp'], 'openai', 1_050_000, 250_000)],
      harnesses: [{id: 'claude', label: 'Claude Code (Anthropic)', runtime: 'claude', capabilities: {compaction: true}},
        {id: 'codex', label: 'Codex (OpenAI)', runtime: 'codex', capabilities: {compaction: true}},
        {id: 'nocomp', label: 'Plain', runtime: 'nocomp', capabilities: {}}],
      default: {runtime: 'claude', model: 'claude-opus-5-5'},
    };
    const bot = (id: string, name: string, m: string, harness: string, extra: Partial<Bot> = {}): Bot => ({id, name: id, slug: id, display_name: name,
      description: '', status: 'active', owner_id: 'ana', repo: `bot-${id}`, can_manage: true, users: [], bot_owners: [], revision: 4, model: m, harness,
      runtime: harness, reasoning_effort: 'medium', task_model: null, thread_mode: 'personal', bot_contact: 'open',
      machine: {runner_id: 'r1', label: 'Box', owner_id: 'ana', generation: 7}, ...extra});
    const fresh = () => [bot('assistant', 'Assistant', 'claude-opus-5-5', 'claude'), bot('researcher', 'Researcher', 'claude-haiku-5-5', 'claude'),
      bot('analyst', 'Analyst', 'claude-opus-5-5', 'claude', {compact_at: 600_000}), bot('reviewer', 'Reviewer', 'gpt-6-sol', 'nocomp')];
    let bots = fresh(), role = 'owner';
    let calls: {what: string; bot: string; body: Body}[] = [];
    const errors: string[] = [];
    const newPage = async (width = 1440) => {
      const page = await browser.newPage({viewport: {width, height: 900}, serviceWorkers: 'block'});
      page.on('pageerror', e => errors.push(e.message));
      await page.route('**/*', route => {
        const req = route.request(), p = new URL(req.url()).pathname;
        const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
        const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
        if (ui && fs.existsSync(uiFile(ui[1]!)))
          return route.fulfill({contentType: ui[1]!.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]!), 'utf8')});
        if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
        if (req.method() === 'POST' && p === '/api/v2/bots') { calls.push({what: 'create', bot: '', body: req.postDataJSON()}); return json({id: 'new-bot', note: 'Added'}); }
        const own = p.match(/^\/api\/v2\/bots\/([^/]+)\/([a-z-]+)$/);
        if (req.method() === 'POST' && own) {
          const body = req.postDataJSON(), e = bots.find(b => b.id === own[1])!, what = own[2]!;
          calls.push({what, bot: e.id, body});
          if (body.expected_revision !== e.revision) return json({error: {detail: 'Bot configuration changed; refresh before saving'}}, 409);
          e.revision++;
          if (what === 'compact-at') { e.compact_at = body.compact_at; return json({bot: e.id, compact_at: body.compact_at, revision: e.revision}); }
          if (what === 'transitions') { Object.assign(e, {model: body.model, harness: body.harness, runtime: body.harness, reasoning_effort: body.effort}); return json({id: 't1', state: 'applied'}); }
          if (what === 'task-model') {
            const t = body.task_model as {harness?: string; model?: string | null; effort?: string | null; compact_at?: number | null} | null;
            e.task_model = !t ? null : {harness: t.harness || '', model: t.model || '', reasoning_effort: t.effort || '', runtime: t.harness || '', compact_at: t.compact_at || null};
            return json({bot: e.id, task_model: e.task_model, revision: e.revision, readiness: null, warning: ''});
          }
          return json({name: e.id, revision: e.revision});
        }
        if (p === '/api/v2/settings/transitions/t1') return json({id: 't1', kind: 'model', state: 'applied', bot: 'researcher', progress: {prepared: 1, total: 1}});
        if (p === '/api/me') return json({id: 'ana', role, name: 'Ana', email: 'ana@example.com', cloud: true, registered: true});
        if (p === '/api/v2/config') return json({version: '0.1.0', app_name: 'Tico', local: true});
        if (p === '/api/employees') return json(role === 'owner' ? bots : bots.map(b => ({...b, can_manage: false})));
        if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana'}], teams: {}});
        if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: []});
        if (p === '/api/v2/status') return json({bots: []});
        if (p === '/api/v2/operations') return json({machines: [{id: 'r1', label: 'Box', owner_id: 'ana', bots: bots.map(b => b.id)}], services: []});
        if (p === '/api/v2/models') return json({...catalog, enabled_providers: ['openai', 'anthropic']});
        if (p === '/api/v2/usage/limits') return json({bots: {}});
        if (p === '/api/v2/settings/history') return json({changes: [], transitions: []});
        if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
        return json(unrouted(p));
      });
      await page.goto('https://tico-ui.test/#/settings');
      await page.waitForFunction(() => typeof S !== 'undefined' && S.route === SETTINGS && S.emps?.length && SETTINGS_DATA.models?.length && !!document.querySelector('#set-bots'));
      await page.evaluate(() => { settingsShow('bots'); renderSettingsBots(); });
      await page.locator('[data-settings-bot=assistant] [data-bot-choice][data-kind=model]').waitFor();
      return page;
    };
    const cell = (page: Page, id: string, kind = 'model') => page.locator(`[data-settings-bot=${id}] [data-bot-choice][data-kind=${kind}]`);
    const capture = async (name: string, target: Page | import('playwright').Locator) => {
      if (!process.env.TICO_UI_RECEIPT_DIR) return;
      fs.mkdirSync(process.env.TICO_UI_RECEIPT_DIR, {recursive: true});
      await target.screenshot({path: require('node:path').join(process.env.TICO_UI_RECEIPT_DIR, name + '.png'), animations: 'disabled'});
    };
    const reloaded = (page: Page, id: string, revision: number) => page.waitForFunction(([b, r]) => S.emps.find(e => e.name === b)?.revision === r, [id, revision] as const);

    let page = await newPage();
    // AC-1, AC-10: model, effort and window; the default follows the model; a saved window shows as saved.
    assert.equal(await closedText(cell(page, 'assistant')), 'Opus 5.5 MED 400K');
    assert.equal(await closedText(cell(page, 'researcher')), 'Haiku 5.5 MED 150K');
    assert.equal(await closedText(cell(page, 'analyst')), 'Opus 5.5 MED 600K');
    // AC-7: no window for a harness that does not compact.
    assert.equal(await closedText(cell(page, 'reviewer')), 'GPT 6 Sol MED');
    assert.match((await cell(page, 'assistant').locator('[data-choice-open]').getAttribute('aria-label'))!, /^Model for Assistant: Claude Code, Opus 5.5, medium effort, compacts at 400k$/);
    assert.deepEqual(await page.locator('[data-bot-choice][data-kind=model] .sb-pick-text').evaluateAll(texts => texts.filter(t => t.scrollWidth > t.clientWidth).map(t => t.textContent)), [],
      'the whole model, effort and window show in the table');
    await capture('desktop-closed', page.locator('#settings-bots-list'));

    // AC-2: one panel; the harness and full effort names; the models (no window: it read like a size to pick); Compact
    // starts on Default, which names the model's default, then the twelve sizes.
    let panel = await openPicker(cell(page, 'assistant'));
    assert.deepEqual(await offered(panel, 'harness'), ['Claude Code', 'Codex', 'Plain']);
    assert.equal(await chosen(panel, 'harness'), 'claude');
    assert.deepEqual((await offered(panel, 'model')).map(t => t.replace(/\s+/g, ' ').trim()), ['Opus 5.5 (default)', 'Haiku 5.5']);
    assert.equal(await chosen(panel, 'model'), 'claude-opus-5-5');
    assert.deepEqual(await offered(panel, 'effort'), ['low', 'medium', 'high', 'xhigh', 'max']);
    const compact = panel.locator('[data-choice-compact]');
    assert.deepEqual(await compact.locator('option').allInnerTexts(), ['Default (400K)', ...SIZES]);
    assert.equal(await compact.inputValue(), '', 'no saved window: Default');
    assert.equal(await panel.locator('[data-choice-apply]').isDisabled(), true, 'nothing changed yet');
    await capture('desktop-open', panel);
    // AC-3: 700K and Apply saves the window alone: no model change, so no fresh session.
    calls = [];
    await compact.selectOption('700000');
    await applyPicker(panel);
    await reloaded(page, 'assistant', 5);
    assert.deepEqual(calls.map(c => [c.what, c.body]), [['compact-at', {compact_at: 700_000, expected_revision: 4}]]);
    assert.equal(await closedText(cell(page, 'assistant')), 'Opus 5.5 MED 700K');
    // Default clears the saved window: the bot compacts at its model's default again.
    panel = await openPicker(cell(page, 'assistant'));
    assert.equal(await compact.inputValue(), '700000');
    calls = [];
    await compact.selectOption('');
    await applyPicker(panel);
    await reloaded(page, 'assistant', 6);
    assert.deepEqual(calls.map(c => [c.what, c.body]), [['compact-at', {compact_at: null, expected_revision: 5}]]);
    assert.equal(await closedText(cell(page, 'assistant')), 'Opus 5.5 MED 400K');

    // AC-8, AC-9: a 600K bot moved to gpt 5.6 sol (500k): the list ends at 500K and the panel says it was lowered.
    panel = await openPicker(cell(page, 'analyst'));
    await choose(panel, 'harness', 'codex');
    await choose(panel, 'model', 'gpt-5-6-sol');
    assert.deepEqual(await compact.locator('option').allInnerTexts(), ['Default (250K)', '100K', '150K', '200K', '250K', '300K', '400K', '500K']);
    assert.equal(await compact.inputValue(), '500000');
    assert.equal(await panel.locator('[data-choice-note]').innerText(), 'Compact was 600K; lowered to 500K to fit GPT 5.6 Sol.');
    await capture('desktop-lowered', panel);
    await panel.locator('[data-choice-cancel]').click();
    // AC-10b: no saved window, opus to haiku: the default follows, and the panel says so.
    panel = await openPicker(cell(page, 'researcher'));
    await choose(panel, 'model', 'claude-opus-5-5');
    assert.equal(await compact.locator('option:checked').innerText(), 'Default (400K)');
    assert.equal(await panel.locator('[data-choice-note]').innerText(), 'The default follows the model: 400K for Opus 5.5.');
    await choose(panel, 'model', 'claude-haiku-5-5');
    assert.equal(await compact.locator('option:checked').innerText(), 'Default (150K)');
    await panel.locator('[data-choice-cancel]').click();
    // AC-7: a harness without compaction: no Compact row, and Apply saves no window.
    panel = await openPicker(cell(page, 'reviewer'));
    assert.equal(await panel.locator('[data-choice-compact-row]').isVisible(), false);
    await panel.locator('[data-choice-cancel]').click();

    // AC-13: the keyboard. Enter opens; arrows move within a row; Enter applies.
    calls = [];
    await cell(page, 'researcher').locator('[data-choice-open]').focus();
    await page.keyboard.press('Enter');
    await page.locator('#sb-choice-pop [data-choice-harness]').waitFor();
    assert.equal(await page.evaluate(() => (document.activeElement as HTMLInputElement).value), 'claude', 'focus starts on the checked harness');
    await page.keyboard.press('Tab');   // Model
    await page.keyboard.press('Tab');   // Effort
    await page.keyboard.press('ArrowRight');
    assert.equal(await chosen(panel, 'effort'), 'high', 'an arrow moves within Effort');
    await page.keyboard.press('ArrowLeft');
    assert.equal(await chosen(panel, 'effort'), 'medium');
    await page.keyboard.press('Tab');   // Compact
    assert.equal(await page.evaluate(() => document.activeElement?.hasAttribute('data-choice-compact')), true);
    await page.keyboard.press('ArrowDown');
    await page.keyboard.press('Enter');
    await reloaded(page, 'researcher', 5);
    assert.deepEqual(calls.map(c => [c.what, c.body.compact_at]), [['compact-at', 100_000]], 'Enter applied the size after Default');

    // AC-10c and AC-4 (Task model cell): Same as bot reads just that; its window saves to the task record.
    await page.locator('[data-settings-bot=assistant] [data-task-open=assistant]').click();
    const task = page.locator('tr[data-task-row=assistant] [data-bot-choice][data-kind=task]');
    assert.equal(await closedText(task), 'Same as bot');
    assert.equal(await page.locator('tr[data-task-row=assistant] [data-task-context]').count(), 0, 'no separate Task context length');
    panel = await openPicker(task);
    assert.equal(await chosen(panel, 'harness'), '');
    assert.equal(await panel.locator('[data-choice-model]').isVisible(), false);
    assert.deepEqual(await compact.locator('option').allInnerTexts(), ['Same as bot', ...SIZES]);
    calls = [];
    await compact.selectOption('150000');
    await applyPicker(panel);
    await reloaded(page, 'assistant', 7);
    assert.deepEqual(calls.map(c => [c.what, c.body]), [['task-model', {task_model: {model: null, effort: null, compact_at: 150_000}, expected_revision: 6}]]);
    assert.equal(await closedText(page.locator('tr[data-task-row=assistant] [data-bot-choice][data-kind=task]')), 'Same as bot 150K');

    // AC-4, the bot editor: no Compact at row; the picker holds its pick until Save bot, which saves the window alone.
    const editor = page.locator('#bot-editor');
    await page.evaluate(() => settingsEditBot('assistant'));
    await page.waitForFunction(() => !!document.querySelector('#bot-editor [data-bot-choice][data-kind=model]'));
    await page.evaluate(() => settingsEditBot('assistant', true));
    const editorModel = editor.locator('[data-bot-choice][data-kind=model]');
    await editorModel.waitFor();
    assert.equal(await editor.locator('[data-bot-compact], [data-task-context]').count(), 0, 'the separate Compact at and Task context length rows are gone');
    calls = [];
    panel = await openPicker(editorModel);
    await panel.locator('[data-choice-compact]').selectOption('300000');
    await applyPicker(panel);
    assert.equal(await closedText(editorModel), 'Opus 5.5 MED 300K');
    assert.deepEqual(calls, [], 'held until Save bot');
    assert.match(await editor.locator('[data-bot-pending]').innerText(), /model/);
    await editor.locator('button[type=submit]:not([data-choice-apply])').click();
    await reloaded(page, 'assistant', 9);
    assert.deepEqual(calls.map(c => c.what), ['definition', 'compact-at'], 'the window alone: no model change');
    assert.equal(calls[1]!.body.compact_at, 300_000);

    // AC-4, new bot: the picker starts on the team default and its window goes with Add bot.
    await page.evaluate(() => { location.hash = '#/settings'; });
    await page.waitForFunction(() => S.route === SETTINGS);
    await page.evaluate(() => settingsEditBot());
    const add = page.locator('#bot-editor [data-add-choice] [data-bot-choice]');
    await add.waitFor();
    assert.equal(await closedText(add), 'Opus 5.5 MED 400K');
    panel = await openPicker(add);
    await choose(panel, 'model', 'claude-haiku-5-5');
    assert.equal(await panel.locator('[data-choice-compact] option:checked').innerText(), 'Default (150K)');
    await panel.locator('[data-choice-compact]').selectOption('700000');
    await applyPicker(panel);
    await page.locator('#bot-editor input[name=display_name]').fill('Planner');
    calls = [];
    await page.locator('#bot-editor button[type=submit]:not([data-choice-apply])').click();
    await page.waitForFunction(() => !(document.querySelector('#bot-editor') as HTMLDialogElement).open);
    // The editor closes, reloads the bots, then says so: wait for that, so its reload cannot land after the reset below.
    await page.locator('.toast', {hasText: 'Added'}).waitFor();
    assert.equal(calls[0]!.what, 'create');
    assert.deepEqual([calls[0]!.body.model, calls[0]!.body.effort, calls[0]!.body.harness, calls[0]!.body.compact_at], ['claude-haiku-5-5', 'medium', 'claude', 700_000]);

    // AC-4, bulk change: the window for every selected bot, after its model; one already on the model gets the window only.
    bots = fresh();
    await page.evaluate(async () => { await loadSettings(); settingsShow('bots'); });
    await page.waitForFunction(() => S.emps.find(e => e.name === 'assistant')?.revision === 4);
    await page.evaluate(() => { SETTINGS_BOTS_VIEW.selected = new Set(['assistant', 'researcher']); settingsBulkModelDialog(); });
    const bulk = page.locator('.settings-bulk-dialog [data-bulk-pick] [data-bot-choice]');
    await bulk.waitFor();
    panel = await openPicker(bulk);
    await choose(panel, 'harness', 'claude');
    await choose(panel, 'model', 'claude-opus-5-5');
    assert.equal(await panel.locator('[data-choice-compact] option').first().innerText(), "Keep each bot's");
    await panel.locator('[data-choice-compact]').selectOption('700000');
    await applyPicker(panel);
    assert.equal(await closedText(bulk), 'Opus 5.5 MED 700K');
    calls = [];
    await page.locator('[data-bulk-apply]').click();
    await page.locator('[data-bulk-summary]:not(:empty)').waitFor();
    assert.deepEqual(calls.map(c => [c.bot, c.what]), [['assistant', 'compact-at'], ['researcher', 'compact-at'], ['researcher', 'transitions']]);
    assert.deepEqual(calls.map(c => c.body.expected_revision), [4, 4, 5], 'the model change follows the window\'s revision');
    await page.locator('.settings-bulk-dialog [data-bulk-close]').first().click();
    await page.close();

    // AC-12: 390px wide, the panel is a sheet with no sideways scroll.
    bots = fresh();
    page = await newPage(390);
    panel = await openPicker(cell(page, 'assistant'));
    assert.equal(await panel.evaluate(pop => pop.scrollWidth <= pop.clientWidth), true, 'no sideways scroll in the panel');
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    const box = await panel.boundingBox();
    assert.ok(box && box.x >= 0 && box.x + box.width <= 390, 'the panel fits the screen');
    await capture('phone-open', page);
    await page.close();

    // AC-14: a person who may not manage the bot opens it read-only.
    role = 'member';
    page = await newPage();
    panel = await openPicker(cell(page, 'assistant'));
    assert.equal(await panel.locator('input:not(:disabled), select:not(:disabled)').count(), 0, 'every row is read-only');
    assert.equal(await panel.locator('[data-choice-apply]').count(), 0, 'no Apply');
    await panel.locator('[data-choice-cancel]').click();
    await page.close();

    assert.deepEqual(errors, []);
    console.log('ok');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
