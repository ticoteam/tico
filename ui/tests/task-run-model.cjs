// A bot's task says the model its runs use (Runs on, backend/task_run_models.py). Whoever the server says may set it
// (`can_set_run_model` in GET /v2/tasks/{id}) picks the task's own model, harness and effort and applies it, or clears
// it back to the bot's task model; anyone else reads what it runs on. Retired and Gateway tier models are not offered.
// The form fits a phone's width.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const SHOTS = process.env.TICO_UI_SHOTS || '';
const launch = () => chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});

const now = Date.now(), at = m => new Date(now - m * 60000).toISOString();
const task = {id: 't-audit', title: 'Audit the billing page', owner: 'bot:ops', requester: 'human:ana', status: 'open',
  lane: 'company', version: 1, created_at: at(30), updated_at: at(5), conversation_id: 'r-audit'};
const MODELS = [
  {id: 'claude-sonnet-5-5', label: 'Sonnet 5.5', harnesses: ['claude'], efforts: ['low', 'medium', 'high'], default_effort: 'high'},
  {id: 'gpt-test', label: 'GPT Test', harnesses: ['codex', 'cursor'], efforts: ['low', 'high'], default_effort: 'low'},
  {id: 'old-model', label: 'Old Model', harnesses: ['claude'], efforts: [], deprecated: true},
  {id: 'tico-fast', label: 'Gateway fast', harnesses: ['claude'], efforts: []}];

// `canSet` is the server's answer for this reader (`can_set_run_model`), whatever their role in the roster says.
async function run(viewport, shot, me, canSet) {
  const browser = await launch();
  try {
    const page = await browser.newPage({viewport, serviceWorkers: 'block'});
    const errors = [], posted = [];
    let runModel = null;
    page.on('pageerror', e => errors.push(e.message));
    await page.route('**/*', async route => {
      const req = route.request(), url = new URL(req.url()), p = url.pathname;
      const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
      if (ui && fs.existsSync(uiFile(ui[1])))
        return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
      if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      if (p === '/api/me') return json(me);
      if (p === '/api/employees') return json([{name: 'ops', display_name: 'Ops', host: 'keeper', status: 'active', can_chat: true, schedules: [], users: [{id: 'ana'}]}]);
      if (p === '/api/issues') return json([]);
      if (p === '/api/v2/conversations') return json({conversations: []});
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      if (p === '/api/v2/models') return json({models: MODELS});
      if (p === '/api/v2/tasks') return json({tasks: [task]});
      if (p === '/api/v2/tasks/t-audit/model' && req.method() === 'POST') {
        const body = JSON.parse(req.postData() || '{}');
        posted.push(body);
        runModel = body.model ? {model: body.model, harness: body.harness, runtime: body.harness, reasoning_effort: body.effort || ''} : null;
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
    await sec.locator('.task-run-now', {hasText: "The bot's task model"}).waitFor();

    if (!canSet) {
      // Someone the server does not let set the model reads what the task runs on, with nothing to pick, whatever
      // their role: the owner too, when the answer for this task is no. The models are read either way, so a form would
      // have appeared by now.
      await page.waitForFunction(() => Array.isArray(TASK_RUN_ROWS));
      assert.equal(await sec.locator('form').count(), 0);
      assert.deepEqual(errors, []);
      return;
    }
    const model = sec.locator('[data-run-model]');
    await model.waitFor();
    const offered = await model.locator('option').evaluateAll(os => os.map(o => o.value));
    assert.deepEqual(offered, ['', 'claude-sonnet-5-5', 'gpt-test'], 'no retired or Gateway tier model');
    assert.equal(await sec.locator('[data-run-model-apply]').isDisabled(), true, 'nothing to apply yet');

    // A model with two harnesses offers both, starting on its first harness and its default effort.
    await model.selectOption('gpt-test');
    await sec.locator('[data-run-harness]').waitFor();
    assert.equal(await sec.locator('[data-run-harness]').inputValue(), 'codex');
    assert.equal(await sec.locator('[data-run-effort]').inputValue(), 'low');
    await sec.locator('[data-run-harness]').selectOption('cursor');
    await sec.locator('[data-run-effort]').selectOption('high');
    const box = await sec.boundingBox();
    assert.ok(box.x >= 0 && box.x + box.width <= viewport.width, `Runs on fits the ${viewport.width} px width`);
    if (shot) await page.screenshot({path: shot});
    await sec.locator('[data-run-model-apply]').click();
    await sec.locator('.task-run-now', {hasText: 'GPT Test'}).waitFor();
    assert.deepEqual(posted.at(-1), {model: 'gpt-test', harness: 'cursor', effort: 'high'});
    assert.match(await sec.locator('.task-run-now').innerText(), /GPT Test · .+ · high/);

    // Back to the bot's task model clears the task's own choice.
    await sec.locator('[data-run-model]').selectOption('');
    await sec.locator('[data-run-model-apply]').click();
    await sec.locator('.task-run-now', {hasText: "The bot's task model"}).waitFor();
    assert.deepEqual(posted.at(-1), {model: null});
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
}

(async () => {
  const shot = name => SHOTS ? require('node:path').join(SHOTS, name) : '';
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
