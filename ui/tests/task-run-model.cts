// A bot's task says the model its runs use (Runs on, backend/task_run_models.py), in the one model picker
// (ui/app/settings-bots-choice.ts, task #476): closed it reads "Same as bot" or "GPT Test HIGH 400K", with no harness,
// dots or "compacts at"; open it is the shared panel, "Same as bot" first, then Harness, Model, Effort and a Compact
// dropdown that starts on "Same as bot" and lists only the sizes the picked model holds. Whoever the server says may set
// it (`can_set_run_model` in GET /v2/tasks/{id}) applies a pick, a window alone or Same as bot; anyone else opens a panel
// with nothing to apply. Retired models are not offered. It fits a phone's width.
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cts') as import('./support/page.cts').Api;
const {unrouted} = require('./support/routes.cts') as import('./support/routes.cts').Api;
const {openPicker, choose, chosen, offered, applyPicker, closedText} = require('./support/model-picker.cts') as import('./support/model-picker.cts').Api;
// Browser-side globals the test reads or calls inside page.evaluate / waitForFunction.
declare const S: {emps: {name: string; revision: number}[]; route: string; me?: unknown};
declare var taskModalShow: (task: {id: string; title: string}) => unknown;
const SHOTS = process.env.TICO_UI_SHOTS || '';
const launch = () => chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined} as import('playwright').LaunchOptions);

const now = Date.now(), at = (m: number) => new Date(now - m * 60000).toISOString();
const task = {id: 't-audit', title: 'Audit the billing page', owner_id: 'bot:ops', requester_id: 'human:ana', status: 'open',
  lane: 'company', version: 1, created_at: at(30), updated_at: at(5), conversation_id: 'r-audit'};
const MODELS = [
  {id: 'claude-sonnet-5-5', label: 'sonnet 5.5', context_tokens: 1_000_000, harnesses: ['claude'], efforts: ['low', 'medium', 'high'], default_effort: 'high'},
  {id: 'gpt-test', label: 'gpt test', context_tokens: 1_050_000, harnesses: ['codex', 'cursor'], efforts: ['low', 'high'], default_effort: 'low'},
  {id: 'gpt-small', label: 'gpt small', context_tokens: 500_000, harnesses: ['codex'], efforts: ['low', 'high'], default_effort: 'low'},
  {id: 'plain-model', label: 'plain model', context_tokens: 1_000_000, harnesses: ['nocomp'], efforts: ['low'], default_effort: 'low'},
  {id: 'old-model', label: 'old model', harnesses: ['claude'], efforts: [], deprecated: true}];
const HARNESSES = [
  {id: 'claude', label: 'Claude Code (Anthropic)', runtime: 'claude', capabilities: {compaction: true}},
  {id: 'codex', label: 'Codex (OpenAI)', runtime: 'codex', capabilities: {compaction: true}},
  {id: 'cursor', label: 'Cursor', runtime: 'cursor', capabilities: {compaction: true}},
  {id: 'nocomp', label: 'Plain', runtime: 'nocomp', capabilities: {}}];
const SIZES = ['100K', '150K', '200K', '250K', '300K', '400K', '500K', '600K', '700K', '800K', '900K', '1M'];

// `canSet` is the server's answer for this reader (`can_set_run_model`), whatever their role in the roster says.
async function run(viewport: {width: number; height: number}, shot: string, me: unknown, canSet: boolean) {
  const browser = await launch();
  try {
    const page = await browser.newPage({viewport, serviceWorkers: 'block'});
    const errors: string[] = [], posted: unknown[] = [];
    let runModel: {model: string; harness: string; runtime: string; reasoning_effort: string; compact_at: number | null} | null = null;
    page.on('pageerror', e => errors.push(e.message));
    await page.route('**/*', async route => {
      const req = route.request(), url = new URL(req.url()), p = url.pathname;
      const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
      if (ui && fs.existsSync(uiFile(ui[1]!)))
        return route.fulfill({contentType: ui[1]!.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]!), 'utf8')});
      if (/\.(?:js|ts)$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: ''});
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      if (p === '/api/me') return json(me);
      if (p === '/api/employees') return json([{name: 'ops', display_name: 'Ops', host: 'keeper', status: 'active', can_chat: true, schedules: [], users: [{id: 'ana'}],
        model: 'claude-sonnet-5-5', harness: 'claude', runtime: 'claude', reasoning_effort: 'high'}]);
      if (p === '/api/issues') return json([]);
      if (p === '/api/v2/conversations') return json({conversations: []});
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      if (p === '/api/v2/models') return json({models: MODELS, harnesses: HARNESSES});
      if (p === '/api/v2/tasks') return json({tasks: [task]});
      if (p === '/api/v2/tasks/t-audit/model' && req.method() === 'POST') {
        const body = JSON.parse(req.postData() || '{}');
        posted.push(body);
        runModel = body.model ? {model: body.model, harness: body.harness, runtime: body.harness, reasoning_effort: body.effort || '', compact_at: body.compact_at ?? null}
          : body.compact_at ? {model: '', harness: '', runtime: '', reasoning_effort: '', compact_at: body.compact_at} : null;
        return json({task: 't-audit', run_model: runModel, readiness: null, warning: ''});
      }
      if (p === '/api/v2/tasks/t-audit')
        return json({task, events: [], can_comment: true, children: [], comments: [], execution: null,
          run_model: runModel, can_set_run_model: canSet});
      if (p.endsWith('/files')) return json({files: []});
      return json(unrouted(p));
    });
    await page.goto('https://tico-ui.test/#/tasks');
    await page.waitForFunction(() => typeof taskModalShow === 'function' && S.emps?.length);
    await page.evaluate(t => taskModalShow(t), task);
    const modal = page.locator('#task-modal'), sec = modal.locator('[data-task-run-model]');
    if (viewport.width <= 390) await page.locator('#task-modal[open] [data-task-tab="info"]').click();   // a phone: Runs on is in Info (task #409)
    const box = sec.locator('[data-bot-choice]');
    await box.waitFor();
    assert.equal(await closedText(box), 'Same as bot', 'no choice of its own: Same as bot, nothing else');
    // The old stacked selects are gone: one picker.
    assert.equal(await sec.locator('select').count(), 0);
    const panel = await openPicker(box);

    if (!canSet) {
      // Someone the server does not let set the model reads what the task runs on, with nothing to apply, whatever
      // their role: the owner too, when the answer for this task is no.
      assert.equal(await panel.locator('[data-choice-apply]').count(), 0, 'no Apply');
      assert.equal(await panel.locator('[data-choice-harness] input:not(:disabled)').count(), 0, 'nothing to pick');
      await panel.locator('[data-choice-cancel]').click();
      assert.deepEqual(posted, []);
      assert.deepEqual(errors, []);
      return;
    }
    // Same as bot first, then the team's harnesses; the bot runs on Sonnet (Claude Code), so Compact offers its sizes
    // after Same as bot.
    assert.deepEqual(await offered(panel, 'harness'), ['Same as bot', 'Claude Code', 'Codex', 'Cursor', 'Plain']);
    assert.equal(await chosen(panel, 'harness'), '');
    const compact = panel.locator('[data-choice-compact]');
    assert.deepEqual(await compact.locator('option').allInnerTexts(), ['Same as bot', ...SIZES]);
    assert.equal(await panel.locator('[data-choice-apply]').isDisabled(), true, 'nothing to apply yet');

    // A harness and a model: the model's own efforts, Default first; retired models are not offered.
    await choose(panel, 'harness', 'codex');
    assert.deepEqual((await offered(panel, 'model')).map(t => t.replace(/\s+/g, ' ').trim()), ['GPT Test', 'GPT Small']);
    await choose(panel, 'model', 'gpt-test');
    assert.deepEqual(await offered(panel, 'effort'), ['Default', 'high']);
    await choose(panel, 'harness', 'cursor');
    await choose(panel, 'model', 'gpt-test');
    await choose(panel, 'effort', 'high');
    assert.deepEqual(await compact.locator('option').allInnerTexts(), ['Same as bot', ...SIZES]);
    await compact.selectOption('400000');
    const fits = (await panel.boundingBox())!;
    assert.ok(fits.x >= 0 && fits.x + fits.width <= viewport.width, `the panel fits the ${viewport.width} px width`);
    if (shot) await page.screenshot({path: shot});
    await applyPicker(panel);
    await page.waitForFunction(() => document.querySelector('[data-task-run-model] .sb-pick-text')?.textContent?.trim() === 'GPT Test HIGH 400K');
    assert.deepEqual(posted.at(-1), {model: 'gpt-test', harness: 'cursor', effort: 'high', compact_at: 400000});

    // A smaller model lowers the window to fit and lists nothing larger.
    let open = await openPicker(sec.locator('[data-bot-choice]'));
    await choose(open, 'harness', 'codex');
    await choose(open, 'model', 'gpt-small');
    assert.deepEqual(await compact.locator('option').allInnerTexts(), ['Same as bot', ...SIZES.slice(0, 7)]);
    assert.equal(await compact.inputValue(), '400000');
    await compact.selectOption('500000');
    // Back to Same as bot keeps the task's own window ("Same as bot 500K")...
    await choose(open, 'harness', '');
    assert.equal(await compact.inputValue(), '500000', 'the window stays when the model goes');
    await applyPicker(open);
    await page.waitForFunction(() => document.querySelector('[data-task-run-model] .sb-pick-text')?.textContent?.trim() === 'Same as bot 500K');
    assert.deepEqual(posted.at(-1), {model: null, compact_at: 500000});
    // ...and Same as bot in Compact clears the task's own choice.
    open = await openPicker(sec.locator('[data-bot-choice]'));
    await compact.selectOption('');
    await applyPicker(open);
    await page.waitForFunction(() => document.querySelector('[data-task-run-model] .sb-pick-text')?.textContent?.trim() === 'Same as bot');
    assert.deepEqual(posted.at(-1), {model: null, compact_at: null});
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
}

(async () => {
  const shot = (name: string) => SHOTS ? require('node:path').join(SHOTS, name) : '';
  const owner = {id: 'ana', name: 'Ana', role: 'owner', cloud: true}, member = {id: 'cara', name: 'Cara', role: 'human', cloud: true};
  const manager = {id: 'ben', name: 'Ben', role: 'human', cloud: true};
  await run({width: 1200, height: 800}, shot('task-run-model-desktop.png'), owner, true);
  await run({width: 390, height: 844}, shot('task-run-model-phone.png'), owner, true);
  await run({width: 1200, height: 800}, '', owner, false);
  await run({width: 1200, height: 800}, '', member, false);
  // Someone who manages the branch's original but not the branch gets the form too.
  await run({width: 1200, height: 800}, '', manager, true);
  console.log('task run model ok');
})().catch(e => { console.error(e); process.exit(1); });
