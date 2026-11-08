// Offline browser regression: a task's page shows its session as a chat. People's lines are bubbles under their names
// (yours without one), the bot's reply opens to the steps behind it, a status change is a one-line marker, and while
// the owner's run works a "Working" line shows its newest step with Stop, which stops that run. Redraws keep an open
// fold's steps and "Stopping…"; someone who may not read the bot's runs is told why its steps are hidden. On a person's
// task the bot that asked it answers: your message shows the bot chat's pending line (Sent, Queued, "<Bot> is working",
// then the reply, or Reply failed), and the composer says before sending when no bot will get it. Where a bot gets the
// comment, the send says how it arrives (steer, queue, defer or goal), and the choice is kept per task.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const SHOTS = process.env.TICO_UI_SHOTS || '';
const launch = () => chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
// Reads the task again once no send or read is in flight: taskChatRead skips while one is, and a read that left before
// the fixture changed paints what the fixture said then.
async function reread(page) {
  await page.waitForFunction(() => !TASK_CHAT.sending && !TASK_CHAT.reading);
  await page.evaluate(() => taskChatRead(TASK_CHAT));
}

const now = Date.now(), at = m => new Date(now - m * 60000).toISOString();
const task = {id: 't-docs', title: 'Ship the docs refresh', owner_id: 'bot:botops', requester_id: 'human:ana', status: 'doing',
  lane: 'company', version: 2, created_at: at(60), updated_at: at(1), conversation_id: 'r-docs'};
const comments = [
  {id: 'k1', conversation_id: 'r-docs', sender_id: 'human:ana', kind: 'say', body: 'Refresh the docs please', created_at: at(50)},
  {id: 'k2', conversation_id: 'r-docs', sender_id: 'human:ben', kind: 'say', body: 'Include the API page too.', created_at: at(40)},
  {id: 'k3', conversation_id: 'r-docs', sender_id: 'bot:botops', kind: 'say', body: 'Rebuilt the guide.', created_at: at(30),
    refs: {turn_id: 'a1'}, run: {steps: 3, tool_calls: 2, took_s: 95}},
  {id: 'k4', conversation_id: 'r-docs', sender_id: 'bot:botops', kind: 'say', body: 'Shared the preview.', created_at: at(20),
    refs: {turn_id: 'a3'}, run: {steps: 1}},
];
const events = [{id: 1, task_id: 't-docs', created_at: at(35), actor: 'bot:botops', field: 'status', old: 'open', new: 'doing', note: ''}];
const execution = {job_id: 'j2', message_id: 'k2', bot: 'botops', run_id: 'a2', state: 'running', label: 'Working', text: '',
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
        return json({task, comments, events, can_comment: true, children: [], execution: !running ? {...execution, state: 'canceled', label: 'Stopped'}
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
      return json(unrouted(p));
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
    assert.match(await live.innerText(), /BotOps is working…\s*Ran hub task create/);
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
    assert.deepEqual(stops, [{run_id: 'a2'}]);
    running = false;
    await reread(page);
    await page.waitForFunction(() => !document.querySelector('#task-modal .task-live'));
    assert.ok(await fold.evaluate(x => x.open), 'an open fold stays open across the redraw');
    // A tab shown again replays what it missed at the caller's pace (the live line's 1.5 s), not the 15 s default.
    const replayed = await page.evaluate(async () => {
      let hidden = false, ran = 0;
      Object.defineProperty(document, 'hidden', {configurable: true, get: () => hidden});
      const wait = ms => new Promise(r => setTimeout(r, ms));
      liveThrottle('pace-test', () => ran++, 300);
      await wait(50);
      hidden = true; liveThrottle('pace-test', () => ran++, 300);
      hidden = false; document.dispatchEvent(new Event('visibilitychange'));
      const shown = Date.now();
      while (ran < 2 && Date.now() - shown < 3000) await wait(20);
      delete document.hidden;
      return {ran, ms: Date.now() - shown};
    });
    assert.equal(replayed.ran, 2, `the hidden call ran within 3 s of the tab showing (${replayed.ms} ms)`);
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
}

// Ana owns a task Head of Engineering asked her; her reply goes to that bot, which is not the task's owner.
async function answered(viewport, shot) {
  const browser = await launch();
  try {
    const page = await browser.newPage({viewport, serviceWorkers: 'block'});
    const errors = [];
    const ask = {id: 't-ask', title: 'Which app is still open?', owner_id: 'human:ana', requester_id: 'bot:lead', status: 'open',
      lane: 'company', version: 1, created_at: at(30), updated_at: at(5), conversation_id: 'r-ask'};
    const said = [{id: 'q1', conversation_id: 'r-ask', sender_id: 'bot:lead', kind: 'say', body: 'Which app is still open?', created_at: at(20)}];
    const mine = {id: 'c2', conversation_id: 'r-ask', sender_id: 'human:ana', recipient_id: 'bot:lead', kind: 'say', body: 'The billing one.', created_at: at(0)};
    const reply = {id: 'r3', conversation_id: 'r-ask', sender_id: 'bot:lead', kind: 'say', body: 'Thanks, closing it.', created_at: at(0)};
    const run = {job_id: 'c2', message_id: 'c2', bot: 'lead', run_id: 'a9', from_person: true, text: '', parts: []};
    const states = {
      idle: {comments: said, execution: null},
      queued: {comments: [...said, mine], execution: {...run, run_id: null, state: 'queued', label: 'Saved — queued'}},
      running: {comments: [...said, mine], execution: {...run, state: 'running', label: 'Working', parts: [{kind: 'tool', text: 'Ran hub task show', at: at(0)}]}},
      replied: {comments: [...said, mine, reply], execution: {...run, state: 'completed', label: 'Complete'}},
      failed: {comments: [...said, mine], execution: {...run, state: 'uncertain', label: 'Reply failed — your message is saved'}},
      botToBot: {comments: [...said, mine], execution: {...run, from_person: false, state: 'queued', label: 'Saved — queued'}},
    };
    let phase = 'idle', to = {bot: 'lead', wakes: true, mention: []}, release = null, sends = 0;
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
      if (p === '/api/employees') return json([{name: 'lead', display_name: 'Head of Engineering', host: 'keeper', status: 'active', can_chat: true, schedules: [], users: [{id: 'ana'}]}]);
      if (p === '/api/issues') return json([]);
      if (p === '/api/v2/conversations') return json({conversations: []});
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      if (p === '/api/v2/tasks') return json({tasks: [ask]});
      if (p === '/api/v2/tasks/t-ask/comments' && req.method() === 'POST')
        { const sent = sends++ ? {...mine, id: 'c4', body: 'Try again please.'} : mine;
          return json({comment: sent, comments: [...said, mine, ...(sent === mine ? [] : [sent])], woke: true}); }
      if (p === '/api/v2/tasks/t-ask') {
        if (release) await release;           // the read after a send waits until "Sent" has been seen
        return json({task: ask, events: [], can_comment: true, children: [], comment_to: to, ...states[phase]});
      }
      if (p.endsWith('/files')) return json({files: []});
      return json(unrouted(p));
    });
    await page.goto('https://tico-ui.test/#/tasks');
    await page.waitForFunction(() => typeof taskModalShow === 'function' && S.emps?.length);
    await page.evaluate(t => taskModalShow(t), ask);
    const thread = page.locator('#task-modal .task-comments'), live = thread.locator('.task-live');
    const hint = page.locator('#task-modal [data-task-chat-to]');
    await thread.locator('.tcomment', {hasText: 'Which app is still open?'}).waitFor();
    assert.equal(await hint.innerText(), '', 'a message that reaches the bot needs no warning');
    assert.equal(await live.count(), 0);

    // Sent, then queued, then working, then the reply: the bot that asked, not the owner.
    let open;
    release = new Promise(r => { open = r; });
    await page.locator('#task-modal textarea').fill('The billing one.');
    await page.locator('#task-modal .p-send').click();
    await live.filter({hasText: 'Sent'}).waitFor();
    phase = 'queued'; release = null; open();
    await live.filter({hasText: 'Queued'}).waitFor();
    assert.equal(await live.locator('.typing.wait').count(), 1, 'a queued run breathes, as in the bot chat');
    phase = 'running';
    await reread(page);
    await live.filter({hasText: 'Head of Engineering is working…'}).waitFor();
    assert.match(await live.innerText(), /Ran hub task show/);
    assert.equal(await live.getAttribute('aria-label'), 'Head of Engineering is working: Ran hub task show');
    if (shot) await page.screenshot({path: shot});
    phase = 'replied';
    await reread(page);
    await thread.locator('.tcomment.bot', {hasText: 'Thanks, closing it.'}).waitFor();
    assert.equal(await live.count(), 0, 'the reply replaces the line');
    assert.equal(await page.locator('#task-modal [data-task-chat-status]').innerText(), '');

    // A turn that errored says so; a bot's message to another bot does not take the line.
    phase = 'failed';
    await reread(page);
    await live.filter({hasText: 'Reply failed — your message is saved'}).waitFor();
    // A new message after a failed one is Sent until its own run is read, not the old "Reply failed".
    release = new Promise(r => { open = r; });
    await page.locator('#task-modal textarea').fill('Try again please.');
    await page.locator('#task-modal .p-send').click();
    await live.filter({hasText: 'Sent'}).waitFor();
    assert.doesNotMatch(await live.innerText(), /Reply failed/);
    release = null; open();
    await live.filter({hasText: 'Reply failed'}).waitFor();     // the fixture's read still has the failed run
    phase = 'botToBot';
    await reread(page);
    await page.waitForFunction(() => !document.querySelector('#task-modal .task-live'));

    // Before sending: no bot at all, or saved for the bot's next run unless the draft @mentions it.
    to = {bot: null, wakes: false, mention: []};
    await reread(page);
    await hint.filter({hasText: 'No bot gets this message'}).waitFor();
    to = {bot: 'lead', wakes: false, mention: ['lead', 'Head of Engineering']};
    await reread(page);
    await hint.filter({hasText: "Saved for Head of Engineering's next run on this task. Write @lead to ask now."}).waitFor();
    await page.locator('#task-modal textarea').fill('@Head of Engineering is it the billing app?');
    assert.equal(await hint.innerText(), '', 'an @mention of the bot that asked wakes it');
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
}

// The send says how a comment reaches its bot: Steer, Queue, Defer <duration>, or Set as goal apart. With no pick nothing
// is sent and the button shows the server's default; a pick is kept per task under a new key (the first version's
// "queue" meant Defer), a bot-filed task's plain comment defers and an @mention queues it, and a task with no bot keeps
// the arrow.
async function deliveryChoice(viewport, shot) {
  const browser = await launch();
  try {
    const page = await browser.newPage({viewport, serviceWorkers: 'block'});
    // A pick saved with the old meaning ("queue" = do not wake) is ignored.
    await page.addInitScript(() => { try { localStorage.setItem('hub.task.delivery', '[["t-plan","queue"]]'); } catch {} });
    const errors = [];
    const plan = {id: 't-plan', title: 'Plan the launch', owner_id: 'human:ana', requester_id: 'bot:lead', status: 'open',
      lane: 'company', version: 1, created_at: at(30), updated_at: at(5), conversation_id: 'r-plan'};
    const said = [{id: 'p1', conversation_id: 'r-plan', sender_id: 'bot:lead', kind: 'say', body: 'What should launch first?', created_at: at(20)}];
    const sent = [], posted = [];
    let to = {bot: 'lead', wakes: true, mention: []};
    let choices = {default: 'queue', default_for: null, steer: true, queue: true, steers: true, goal: true};
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
      if (p === '/api/employees') return json([{name: 'lead', display_name: 'Head of Engineering', host: 'keeper', status: 'active', can_chat: true, schedules: [], users: [{id: 'ana'}]}]);
      if (p === '/api/issues') return json([]);
      if (p === '/api/v2/conversations') return json({conversations: []});
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      if (p === '/api/v2/tasks') return json({tasks: [plan]});
      if (p === '/api/v2/tasks/t-plan/comments' && req.method() === 'POST') {
        const body = JSON.parse(req.postData() || '{}');
        posted.push(body);
        sent.push({id: 'p' + (sent.length + 2), conversation_id: 'r-plan', sender_id: 'human:ana', recipient_id: 'bot:lead', kind: 'say', body: body.text, created_at: at(0)});
        const way = body.delivery && body.delivery !== 'goal' ? body.delivery : 'queue';
        return json({comment: sent.at(-1), comments: [...said, ...sent], woke: false, sent_as: {delivery: way, until: null}});
      }
      if (p === '/api/v2/tasks/t-plan')
        return json({task: plan, events: [], can_comment: true, children: [], comment_to: to, comment_delivery: choices, comments: [...said, ...sent], execution: null});
      if (p.endsWith('/files')) return json({files: []});
      return json(unrouted(p));
    });
    await page.goto('https://tico-ui.test/#/tasks');
    await page.waitForFunction(() => typeof taskModalShow === 'function' && S.emps?.length);
    await page.evaluate(t => taskModalShow(t), plan);
    const modal = page.locator('#task-modal'), main = modal.locator('[data-task-main]'), caret = modal.locator('[data-task-caret]');
    const menu = modal.locator('[data-task-menu]');
    const ways = () => menu.locator('[data-deliver]').evaluateAll(xs => xs.map(x => x.dataset.deliver));
    const send = async text => {
      await modal.locator('textarea').fill(text);
      await main.click();
      await modal.locator('.task-comments', {hasText: text}).waitFor();
      return posted.at(-1);
    };
    await modal.locator('.task-comments', {hasText: 'What should launch first?'}).waitFor();

    // No pick (the old key is ignored): the button shows the server's default and the comment carries no delivery.
    assert.equal(await main.innerText(), 'Queue');
    assert.deepEqual(await send('Which is first?'), {text: 'Which is first?', public: false});
    assert.equal(await page.evaluate(() => localStorage.getItem('hub.task.delivery.v2')), null);

    // The menu: Steer, Queue, Defer and its durations (24h by default), then Set as goal apart; upward, inside the width,
    // every target at least 34 px.
    await caret.click();
    assert.deepEqual(await ways(), ['steer', 'queue', 'defer:24h', 'defer:30m', 'defer:1h', 'defer:4h', 'defer:24h', 'defer:2d', 'defer:7d', 'defer:never', 'goal']);
    assert.equal(await menu.locator('.sep + [data-deliver="goal"]').count(), 1, 'Set as goal sits below a separator');
    const box = await menu.boundingBox(), arrow = await caret.boundingBox();
    assert.ok(box.y + box.height <= arrow.y, 'the menu opens upward, over the thread');
    assert.ok(box.x >= 0 && box.x + box.width <= viewport.width, `the menu fits the ${viewport.width} px width`);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'no sideways scroll');
    await menu.evaluate(m => Promise.all(m.getAnimations().map(a => a.finished)));       // past the menu's pop-in
    const small = await menu.locator('button').evaluateAll(bs => bs.map(b => b.getBoundingClientRect()).filter(r => r.height < 34 || r.width < 34).map(r => `${r.width}x${r.height}`));
    assert.deepEqual(small, [], 'every menu target is at least 34 px');
    if (shot) await page.screenshot({path: shot});
    // Escape closes the menu, not the task.
    await page.keyboard.press('Escape');
    assert.equal(await menu.isHidden(), true);
    assert.equal(await modal.isVisible(), true, 'Escape in the menu leaves the task open');

    // Queue picked: sent as queue, and kept for this task under the new key.
    await caret.click();
    await menu.locator('[data-deliver="queue"]').click();
    assert.equal(await menu.isHidden(), true);
    assert.equal((await send('Billing first, then the docs.')).delivery, 'queue');
    assert.equal(await page.evaluate(() => localStorage.getItem('hub.task.delivery.v2')), '[["t-plan","queue"]]');

    // Defer 4h: the button shows the duration, the comment carries it, and the pick stays for the next one.
    await caret.click();
    await menu.locator('.p-for [data-deliver="defer:4h"]').click();
    assert.equal(await main.innerText(), 'Defer 4h');
    assert.deepEqual(await send('After lunch is fine.'), {text: 'After lunch is fine.', public: false, delivery: 'defer', defer_for: '4h'});
    assert.equal(await main.innerText(), 'Defer 4h');
    // A press outside closes the menu.
    await caret.click();
    await modal.locator('.task-comments').click({position: {x: 5, y: 5}});
    assert.equal(await menu.isHidden(), true);

    // Set as goal goes as the goal of the task's session.
    await caret.click();
    await menu.locator('[data-deliver="goal"]').click();
    assert.equal((await send('Finish the billing migration.')).delivery, 'goal');

    // A task the bot filed: a plain comment defers 24h, an @mention of the bot flips it to queue; nothing is picked, so
    // nothing is sent and the server reads the text.
    await page.evaluate(() => localStorage.removeItem('hub.task.delivery.v2'));
    choices = {default: 'defer', default_for: '24h', steer: true, queue: true, steers: true, goal: false};
    to = {bot: 'lead', wakes: false, mention: ['lead']};
    await reread(page);
    await page.waitForFunction(() => document.querySelector('#task-modal [data-task-main]').textContent === 'Defer 24h');
    await modal.locator('textarea').fill('Billing first; @lead, is the API page in?');
    assert.equal(await main.innerText(), 'Queue');
    await main.click();
    await modal.locator('.task-comments', {hasText: 'Billing first; @lead'}).waitFor();
    assert.equal('delivery' in posted.at(-1), false);
    assert.equal(await main.innerText(), 'Defer 24h', 'the empty box is back to the default');

    // A reader who may not wake the bot only defers.
    choices = {default: 'defer', default_for: 'never', steer: false, queue: false, steers: false, goal: false};
    await reread(page);
    await modal.locator('textarea').fill('@lead too');
    assert.match(await main.innerText(), /^Defer/);
    await caret.click();
    assert.deepEqual((await ways()).filter(w => !w.startsWith('defer')), []);
    await page.keyboard.press('Escape');
    await modal.locator('textarea').fill('');

    // A task with no bot keeps the plain arrow, and its comment carries no delivery.
    to = {bot: null, wakes: false, mention: []}; choices = null;
    await reread(page);
    await modal.locator('[data-task-chat-to]').filter({hasText: 'No bot gets this message'}).waitFor();
    assert.equal(await caret.isHidden(), true);
    assert.ok(await main.isVisible(), 'the plain arrow stays');
    assert.equal('delivery' in (await send('Anyone around?')), false);
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
}

(async () => {
  const shot = name => SHOTS ? require('node:path').join(SHOTS, name) : '';
  await run({width: 1200, height: 800}, shot('task-session-desktop.png'));
  await run({width: 390, height: 844}, shot('task-session-phone.png'));
  await answered({width: 1200, height: 800}, shot('task-session-answered.png'));
  await answered({width: 390, height: 844}, shot('task-session-answered-phone.png'));
  await deliveryChoice({width: 1200, height: 800}, shot('task-session-delivery.png'));
  await deliveryChoice({width: 390, height: 844}, shot('task-session-delivery-phone.png'));
  console.log('ok');
})().catch(e => { console.error(e); process.exit(1); });
