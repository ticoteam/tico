// Offline browser regression: a task's page shows its session as a chat. People's lines are bubbles under their names
// (yours without one), the bot's reply opens to the steps behind it, a status change is a one-line marker, and while
// the owner's run works a "Working" line shows its newest step with Stop, which stops that run. Redraws keep an open
// fold's steps and "Stopping…"; someone who may not read the bot's runs is told why its steps are hidden.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const SHOTS = process.env.TICO_UI_SHOTS || '';
const launch = () => chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});

const now = Date.now(), at = m => new Date(now - m * 60000).toISOString();
const task = {id: 't-docs', title: 'Ship the docs refresh', owner: 'bot:botops', requester: 'human:ana', status: 'doing',
  lane: 'company', version: 2, created: at(60), updated: at(1), conversation_id: 'r-docs'};
const comments = [
  {id: 'k1', conversation_id: 'r-docs', from_actor: 'human:ana', kind: 'say', body: 'Refresh the docs please', created: at(50)},
  {id: 'k2', conversation_id: 'r-docs', from_actor: 'human:ben', kind: 'say', body: 'Include the API page too.', created: at(40)},
  {id: 'k3', conversation_id: 'r-docs', from_actor: 'bot:botops', kind: 'say', body: 'Rebuilt the guide.', created: at(30),
    refs: {turn_id: 'a1'}, run: {steps: 3, tool_calls: 2, took_s: 95}},
  {id: 'k4', conversation_id: 'r-docs', from_actor: 'bot:botops', kind: 'say', body: 'Shared the preview.', created: at(20),
    refs: {turn_id: 'a3'}, run: {steps: 1}},
];
const events = [{id: 1, task_id: 't-docs', ts: at(35), actor: 'bot:botops', field: 'status', old: 'open', new: 'doing', note: ''}];
const execution = {job_id: 'j2', message_id: 'k2', bot: 'botops', attempt_id: 'a2', state: 'running', label: 'Working', text: '',
  parts: [{kind: 'progress', text: 'Checking the API page.', at: at(1)}, {kind: 'tool', text: 'Ran hub task create', at: at(1)}]};

async function run(viewport, shot) {
  const browser = await launch();
  try {
    const page = await browser.newPage({viewport, serviceWorkers: 'block'});
    const errors = [], stops = [], stepReads = [];
    let running = true, live2 = false;
    page.on('pageerror', e => errors.push(e.message));
    await page.route('**/*', async route => {
      const req = route.request(), url = new URL(req.url()), p = url.pathname;
      const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
      if (ui && fs.existsSync(uiFile(ui[1])))
        return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
      if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      if (p === '/api/me') return json({id: 'ana', name: 'Ana', role: 'owner', cloud: true});
      if (p === '/api/employees') return json([{name: 'botops', display_name: 'BotOps', host: 'keeper', status: 'active', can_chat: true, schedules: [], users: [{id: 'ana'}]}]);
      if (p === '/api/issues') return json([]);
      if (p === '/api/v2/conversations') return json({conversations: []});
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      if (p === '/api/v2/tasks') return json({tasks: [task]});
      if (p === '/api/v2/tasks/t-docs')
        return json({task, comments, events, can_comment: true, children: [], execution: !running ? {...execution, state: 'cancelled', label: 'Stopped'}
          : live2 ? {...execution, parts: [...execution.parts, {kind: 'tool', text: 'Ran grep', at: at(0)}]} : execution});
      const steps = p.match(/^\/api\/v2\/turns\/([^/]+)\/steps$/);
      if (steps) {
        stepReads.push(steps[1]);
        if (steps[1] === 'a3') return json({error: {code: 'forbidden', detail: 'Forbidden'}}, 403);
        if (steps[1] === 'a2') return json({turn_id: 'a2', steps: [{kind: 'tool', tool: 'Grep', text: 'api page', at: at(1)}]});
        return json({turn_id: steps[1], steps: [{kind: 'tool', tool: 'Bash', text: 'make docs', at: at(31)}, {kind: 'said', text: 'Rebuilt the guide.', at: at(30)}]});
      }
      if (p === '/api/v2/bots/botops/stop' && req.method() === 'POST') { stops.push(req.postDataJSON()); return json({ok: true}); }
      if (p.endsWith('/files')) return json({files: []});
      return json({});
    });
    await page.goto('https://tico-ui.test/#/bot/botops');
    await page.waitForFunction(() => typeof taskModalShow === 'function' && S.emps?.length);
    await page.evaluate(t => taskModalShow(t), task);
    const thread = page.locator('#task-modal .task-comments');
    await thread.locator('.tcomment', {hasText: 'Rebuilt the guide.'}).waitFor();

    // People: bubbles on the right; another person's carries their name, yours none. The bot's reads as page text.
    const ben = thread.locator('.tcomment.tmsg.you', {hasText: 'Include the API page too.'});
    assert.match(await ben.locator('.tcomment-who').innerText(), /ben/i, 'another person is named');
    assert.equal(await thread.locator('.tcomment.tmsg.from-me .bubble.you', {hasText: 'Refresh the docs please'}).count(), 1);
    assert.ok(!(await thread.locator('.tcomment.from-me .tcomment-who').isVisible()), 'your own line needs no name');
    assert.equal(await thread.locator('.tcomment.tmsg.bot .bubble.bot', {hasText: 'Rebuilt the guide.'}).count(), 1);

    // A status change is a one-line marker between the messages, in time order.
    const order = await thread.evaluate(t => [...t.children].map(x => x.textContent.replace(/\s+/g, ' ').trim()));
    const mark = order.findIndex(x => /moved it to Doing/.test(x));
    assert.ok(mark > order.findIndex(x => /Include the API page/.test(x)) && mark < order.findIndex(x => /Rebuilt the guide/.test(x)), 'the marker sits where it happened');
    assert.equal(await thread.locator('.tcomment.tmark', {hasText: 'moved it to Doing'}).count(), 1);

    // The reply opens to its steps, read once.
    const fold = thread.locator('.tcomment.bot details.run-steps[data-turn="a1"]');
    assert.match(await fold.locator('summary').innerText(), /3 steps · 2 tool calls · 2m/);
    await fold.locator('summary').click();
    await fold.locator('.steps-list .step', {hasText: 'make docs'}).waitFor();
    assert.deepEqual(stepReads, ['a1']);

    // A reader without Read on the bot is told why its steps are hidden.
    const hidden = thread.locator('.tcomment.bot details.run-steps[data-turn="a3"]');
    await hidden.locator('summary').click();
    await hidden.locator('.step.err', {hasText: 'Only people who may read this bot’s runs see its steps.'}).waitFor();

    // The live run: "Working" and its newest step, opening to the steps so far.
    const live = thread.locator('.task-live');
    if (shot) await page.screenshot({path: shot});
    assert.match(await live.innerText(), /Working…\s*Ran hub task create/);
    await live.locator('summary').click();
    await live.locator('.steps-list .step', {hasText: 'api page'}).waitFor();
    // Stop stops that run. Until it has ended the run moves on: the redraw shows its new step, keeps the steps read so
    // far (never "Loading…") and keeps "Stopping…". Once it has ended, the line goes.
    await live.locator('[data-task-stop]').click();
    await live.locator('[data-task-stop]', {hasText: 'Stopping…'}).waitFor();
    live2 = true;
    const kept = await page.evaluate(async () => { await taskChatRead(TASK_CHAT); return document.querySelector('#task-modal .task-live .steps-list').textContent; });
    assert.match(kept, /api page/, 'the open live fold keeps its steps while they are read again');
    assert.match(await live.innerText(), /Ran grep/, 'the redraw drew the new step');
    assert.equal(await live.locator('[data-task-stop]').innerText(), 'Stopping…');
    assert.ok(await live.locator('[data-task-stop]').isDisabled());
    assert.deepEqual(stops, [{attempt_id: 'a2'}]);
    running = false;
    await page.evaluate(() => taskChatRead(TASK_CHAT));
    await page.waitForFunction(() => !document.querySelector('#task-modal .task-live'));
    assert.ok(await fold.evaluate(x => x.open), 'an open fold stays open across the redraw');
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
}

(async () => {
  const shot = name => SHOTS ? require('node:path').join(SHOTS, name) : '';
  await run({width: 1200, height: 800}, shot('task-session-desktop.png'));
  await run({width: 390, height: 844}, shot('task-session-phone.png'));
  console.log('ok');
})().catch(e => { console.error(e); process.exit(1); });
