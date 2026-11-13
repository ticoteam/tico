import type * as PageSupport from './support/page.cts';
import type * as Routes from './support/routes.cts';
// Settings > Bots, task #237: the Fallback picker has a Compact list, "Same as bot" first, offering only the sizes the
// fallback model holds and only for a harness that compacts; its size goes with the /fallback body as `compact_at`
// from the Bots table's picker and from the bot editor's Save, and the closed button shows it. Bulk change's picker
// starts on "Keep each bot's model" with a Compact list on "Keep each bot's"; a window picked alone saves each bot's
// window without changing its model.
const {chromium}: typeof import('playwright') = require('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const fs: typeof import('node:fs') = require('node:fs');
const {html, uiFile} = require('./support/page.cts') as PageSupport.Api;
const {unrouted} = require('./support/routes.cts') as Routes.Api;
const {openPicker, choose, offered, applyPicker, closedText} = require('./support/model-picker.cts') as import('./support/model-picker.cts').Api;
type Call = {what: string | undefined; bot: string; body: Record<string, unknown>};
declare const S: {emps: {id: string; revision?: number; fallback?: {model: string; compact_at?: number}}[]};
declare const SETTINGS_DATA: {models?: unknown[]};
declare function settingsEditBot(id: string, edit?: boolean): void;
declare function loadSettings(): void;
const SIZES = ['100K', '150K', '200K', '250K', '300K', '400K', '500K', '600K', '700K', '800K', '900K', '1M'];

(async () => {
  const browser = await chromium.launch({headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 900}, serviceWorkers: 'block'});
    const errors: string[] = [];
    page.on('pageerror', e => errors.push(e.message));
    const model = (id: string, harness: string, provider: string, context_tokens: number) => ({id, label: id.replace(/-/g, ' '), runtime: harness,
      provider, efforts: ['low', 'high'], default_effort: 'high', harnesses: [harness], context_tokens});
    const catalog = {
      models: [model('opus-5-5', 'claude', 'anthropic', 1_000_000), model('small-5', 'claude', 'anthropic', 500_000),
        model('gpt-6-luna', 'codex', 'openai', 1_050_000)],
      harnesses: [{id: 'codex', label: 'Codex (OpenAI)', runtime: 'codex', capabilities: {compaction: false}},
        {id: 'claude', label: 'Claude Code (Anthropic)', runtime: 'claude', capabilities: {compaction: true}}],
      default: {runtime: 'claude', model: 'opus-5-5'},
    };
    const bot = (name: string) => ({id: name, name, slug: name, display_name: name[0]!.toUpperCase() + name.slice(1), description: '',
      status: 'active', owner_id: 'ana', repo: 'bot-' + name, can_manage: true, users: [], bot_owners: [], revision: 4, model: 'opus-5-5',
      harness: 'claude', reasoning_effort: 'high', thread_mode: 'personal', bot_contact: 'open',
      machine: {runner_id: 'r1', label: 'Box', owner_id: 'ana', generation: 7}} as {[key: string]: unknown; id: string; revision: number});
    const bots = [bot('helper'), bot('scout')];
    let calls: Call[] = [];
    await page.route('**/*', route => {
      const req = route.request(), p = new URL(req.url()).pathname;
      const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
      if (ui && fs.existsSync(uiFile(ui[1]!)))
        return route.fulfill({contentType: ui[1]!.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]!), 'utf8')});
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      const write = req.method() === 'POST' && p.match(/^\/api\/v2\/bots\/([^/]+)\/([^/]+)$/);
      if (write) {
        const body = req.postDataJSON(), b = bots.find(row => row.id === write[1])!, what = write[2];
        calls.push({what, bot: b.id, body});
        if (body.expected_revision !== b.revision) return json({error: {detail: 'Bot configuration changed; refresh before saving'}}, 409);
        if (what === 'fallback') {
          const f = body.fallback;
          b.fallback = f ? {harness: f.harness, model: f.model, reasoning_effort: f.effort, runtime: f.harness, ...(f.compact_at ? {compact_at: f.compact_at} : {})} : undefined;
          return json({bot: b.id, fallback: b.fallback || null, revision: ++b.revision});
        }
        if (what === 'compact-at') b.compact_at = body.compact_at;
        return json({bot: b.id, revision: ++b.revision});
      }
      if (p === '/api/me') return json({id: 'ana', role: 'owner', name: 'Ana', email: 'ana@example.com', cloud: true, registered: true});
      if (p === '/api/v2/config') return json({version: '0.1.0', app_name: 'Tico', local: true});
      if (p === '/api/employees') return json(bots);
      if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana'}], teams: {}});
      if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: []});
      if (p === '/api/v2/status') return json({bots: []});
      if (p === '/api/v2/operations') return json({machines: [{id: 'r1', label: 'Box', owner_id: 'ana', bots: ['helper', 'scout']}], services: []});
      if (p === '/api/v2/models') return json({...catalog, enabled_providers: ['openai', 'anthropic']});
      if (p === '/api/v2/providers') return json({enabled: ['openai', 'anthropic'], default: catalog.default, revision: 1,
        providers: [{id: 'openai', label: 'OpenAI'}, {id: 'anthropic', label: 'Anthropic'}]});
      if (p === '/api/v2/usage/limits') return json({bots: {}});
      if (p === '/api/v2/settings/history') return json({changes: [], transitions: []});
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      return json(unrouted(p));
    });
    await page.goto('https://tico-ui.test/#/settings/bots');
    await page.waitForFunction(() => typeof S !== 'undefined' && S.emps?.some(e => e.id === 'helper') && SETTINGS_DATA.models?.length);
    const compactTexts = (root: import('playwright').Locator) => root.locator('[data-choice-compact] option').allInnerTexts();

    // 1. The Bots table's Fallback picker: Same as bot first, every size for a 1M model, then a 500K model lowers 700K
    //    to 500K and lists nothing larger; Apply sends it, and the closed button shows it.
    const cell = page.locator('.sb-cell-fallback').filter({has: page.locator('[data-bot-choice="helper"]')}).locator('[data-bot-choice]');
    let pop = await openPicker(cell);
    const pick = pop.locator('[data-choice-compact]');
    await choose(pop, 'harness', 'claude');
    await choose(pop, 'model', 'opus-5-5');
    assert.equal(await pick.inputValue(), '', 'Same as bot until a size is picked');
    assert.deepEqual(await compactTexts(pop), ['Same as bot', ...SIZES]);
    await pick.selectOption('700000');
    await choose(pop, 'model', 'small-5');
    assert.equal(await pick.inputValue(), '500000', 'lowered to fit the 500K model');
    assert.deepEqual(await compactTexts(pop), ['Same as bot', ...SIZES.slice(0, 7)]);
    assert.match(await pop.locator('[data-choice-note]').innerText(), /lowered to 500K/);
    // A harness that does not compact at a size keeps the Compact row, with Same as bot alone and a line saying why.
    await choose(pop, 'harness', 'codex');
    assert.equal(await pop.locator('[data-choice-compact-row]').isVisible(), true, 'the row stays');
    assert.deepEqual(await compactTexts(pop), ['Same as bot'], 'Codex here does not compact');
    assert.equal(await pick.isDisabled(), true);
    assert.equal(await pop.locator('[data-choice-note]').innerText(), 'Codex compacts on its own.');
    await choose(pop, 'harness', 'claude');
    await choose(pop, 'model', 'small-5');
    await pick.selectOption('300000');
    await applyPicker(pop);
    await page.waitForFunction(() => S.emps.find(e => e.id === 'helper')?.fallback?.compact_at === 300000);
    assert.deepEqual(calls.map(c => [c.what, c.body]), [['fallback', {fallback: {harness: 'claude', model: 'small-5', effort: 'high', compact_at: 300000}, expected_revision: 4}]]);
    assert.match(await closedText(page.locator('.sb-cell-fallback [data-bot-choice="helper"]')), /300K/);

    // 2. The bot editor: only the window changes, back to Same as bot, and Save sends the fallback without one.
    calls = [];
    await page.evaluate(() => settingsEditBot('helper'));
    await page.locator('#pane-settings').waitFor();
    await page.waitForFunction(() => !!document.querySelector('#bot-editor [data-bot-status]'));
    await page.evaluate(() => settingsEditBot('helper', true));
    const row = page.locator('#bot-editor [data-bot-choice][data-kind=fallback]');
    pop = await openPicker(row);
    assert.equal(await pop.locator('[data-choice-compact]').inputValue(), '300000', 'the editor opens on the saved window');
    await pop.locator('[data-choice-compact]').selectOption('');
    await applyPicker(pop);
    await page.locator('#bot-editor button[type=submit]').click();
    await page.waitForFunction(() => document.querySelector('#bot-editor [data-bot-status]')?.textContent === 'Saved');
    assert.deepEqual(calls[0], {what: 'fallback', bot: 'helper', body: {fallback: {harness: 'claude', model: 'small-5', effort: 'high'}, expected_revision: 5}});
    await page.waitForFunction(() => S.emps.find(e => e.id === 'helper')?.fallback?.compact_at === undefined);

    // 3. Bulk change: Keep each bot's model and Keep each bot's window first; a window alone saves each bot's own
    //    window and changes no model.
    calls = [];
    await page.evaluate(() => { location.hash = '#/settings/bots'; loadSettings(); });
    await page.locator('[data-bots-select-all]').check();
    await page.locator('[data-bulk-model]').click();
    const dialog = page.locator('.settings-bulk-dialog');
    assert.equal(await dialog.locator('[data-bulk-apply]').isDisabled(), true, 'nothing to change yet');
    pop = await openPicker(dialog.locator('[data-bulk-pick] [data-bot-choice]'));
    assert.equal((await offered(pop, 'harness'))[0], "Keep each bot's model");
    const compact = pop.locator('[data-choice-compact]');
    assert.equal(await compact.inputValue(), '');
    assert.deepEqual(await compact.locator('option').allInnerTexts(), ["Keep each bot's", ...SIZES]);
    await compact.selectOption('300000');
    await applyPicker(pop);
    assert.match(await dialog.locator('[data-bulk-destination]').innerText(), /Each bot's model · compacts at 300K/);
    await dialog.locator('[data-bulk-apply]').click();
    await dialog.locator('[data-bulk-close]', {hasText: 'Done'}).waitFor();
    assert.deepEqual(calls.map(c => [c.what, c.bot, c.body.compact_at]).sort(), [['compact-at', 'helper', 300000], ['compact-at', 'scout', 300000]]);
    assert.match(await dialog.locator('[data-bulk-bot=scout]').innerText(), /compacts at 300K/);
    assert.deepEqual(errors, []);
    console.log('settings fallback compact ok');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
