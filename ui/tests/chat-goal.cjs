// Offline browser test of a chat's pinned goal and the "/" menu, against a mocked API
// (GET/POST /api/v2/conversations/{id}/goal, the watch stream's `goal` event, and `command: true` on a chat send).
// Sets a goal from the target, checks the 3-line clamp and tap to expand, pauses and clears it, sees a met goal fold
// into one line, filters and picks commands, and repeats the bar and menu at phone width.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const launch = () => chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
const LONG = 'Ship the Acme onboarding checklist: write the welcome email, set up the three sample projects, '
  + 'check every link in the help pages, fix any broken ones, then post a short summary in the chat with what changed '
  + 'and what still needs a person. Keep going until all of it is done. '
  + 'Document the result of every check, include the corrected links in the summary, and explain any remaining '
  + 'setup steps so the next person can follow the checklist without needing the original conversation.';
const COMMANDS = [
  {name: 'goal', args: '<objective>', help: 'Pin a goal the bot works toward', kind: 'tico', sub: ['pause', 'resume', 'clear', 'edit']},
  {name: 'new', help: 'New chat', kind: 'tico'},
  {name: 'task', args: '<title>', help: 'Make a task for this bot', kind: 'tico'},
  {name: 'help', help: 'Show commands', kind: 'tico'},
  {name: 'compact', help: 'Compact the thread', kind: 'harness'},
  {name: 'model', args: '<name>', help: 'Switch model', kind: 'harness'},
];

async function open(browser, viewport, touch = false, {empty = false, messages = null, goal = null, theme = '', supported = true, readiness = {ready: true, goals: true}} = {}) {
  const page = await browser.newPage({viewport, serviceWorkers: 'block', ...(touch ? {hasTouch: true, isMobile: true} : {})});
  if (theme) await page.addInitScript(t => { try { localStorage.setItem('tico.theme', t); } catch {} }, theme);
  const api = {errors: [], goalPosts: [], sends: [], created: [], goal, streamGoal: null, page, room: !empty, held: []};
  const tick = setInterval(() => {
    const g = api.streamGoal, route = api.held.at(-1);
    if (!g || !route) return;
    api.streamGoal = null; api.goal = g; api.held = [];
    route.fulfill({contentType: 'text/event-stream', body: `id: 1\nevent: messages\ndata: ${JSON.stringify({seq: 1, conversation_id: 'c1', goal_id: g.id, goal: g})}\n\n`}).catch(() => {});
  }, 50);
  page.on('close', () => clearInterval(tick));
  page.on('pageerror', e => api.errors.push(e.message));
  const now = () => new Date().toISOString();
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname;
    const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1])))
      return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
    if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    if (p === '/api/me') return json({id: 'ana', name: 'Ana', role: 'owner', cloud: true});
    if (p === '/api/employees') return json([{name: 'ops', display_name: 'Ops', host: 'keeper', status: 'active', can_chat: true, schedules: [], goal_active: false, resolved_runtime: 'codex',
      machine: {runner_id: 'r1', label: 'Acme box', operator: 'ana'}, readiness}]);
    if (p.startsWith('/vendor/fonts/') && fs.existsSync(uiFile(p.slice(1)))) return route.fulfill({contentType: 'font/woff2', body: fs.readFileSync(uiFile(p.slice(1)))});
    if (p === '/api/issues') return json([]);
    const room = {id: 'c1', kind: 'chat', scope: 'personal', participants: ['human:ana', 'bot:ops']};
    if (p === '/api/v2/conversations' && req.method() === 'POST') {   // the bot's own room, made by the first goal
      api.created.push(req.postDataJSON()); api.room = true; return json(room);
    }
    if (p === '/api/v2/conversations')
      return json({conversations: url.searchParams.get('chat_with') && api.room ? [room] : []});
    if (p.endsWith('/snapshot')) return json({messages: messages || (empty && !api.created.length ? [] : [{id: 'm0', from_actor: 'bot:ops', body: 'Ready when you are.', created: '2026-10-01T09:00:00Z'}]), execution: null});
    // Live events (ui/app/live.js): held open; a goal the stream reports goes out as the change it is, on the newest one.
    if (p === '/api/v2/events') { api.held.push(route); return; }
    if (p === '/api/v2/conversations/c1/goal') {
      if (req.method() === 'GET') return json({goal: api.goal, supported, commands: supported ? COMMANDS : COMMANDS.filter(c => c.name !== 'goal')});
      const body = req.postDataJSON(); api.goalPosts.push(body);
      if (!supported) return json({error: {code: 'goal_unsupported', detail: "This bot's harness doesn't support goals"}}, 409);
      if (api.slowGoal) await new Promise(done => setTimeout(done, api.slowGoal));
      const status = {set: 'active', edit: api.goal?.status || 'active', pause: 'paused', resume: 'active', clear: 'cleared'}[body.action];
      api.goal = {id: 'g1', conversation_id: 'c1', bot: 'ops', objective: body.objective || api.goal?.objective, status, note: '',
                  set_by: 'human:ana', set_at: api.goal?.set_at || now(), updated_at: now(), ended_at: status === 'cleared' ? now() : null};
      const out = {goal: api.goal};
      if (status === 'cleared') api.goal = null;
      return json(out);
    }
    if (p === '/api/v2/chat/ops') {
      const body = req.postDataJSON(); api.sends.push(body);
      return json({conversation: {id: 'c1'}, message: {id: 'm' + api.sends.length, from_actor: 'human:ana', body: body.text, created: now()}});
    }
    return json({});
  });
  await page.goto('https://tico-ui.test/#/bot/ops');
  await page.locator('#chat-composer textarea').waitFor();
  return api;
}
const clampOf = page => page.locator('#chat-goal .cg-text').evaluate(el => {
  const cs = getComputedStyle(el), line = parseFloat(cs.lineHeight);
  return {clamp: cs.webkitLineClamp, lines: Math.round(el.clientHeight / line), cut: el.scrollHeight > el.clientHeight + 1};
});

async function desktop(browser) {
  const api = await open(browser, {width: 1280, height: 860}), {page} = api;
  const target = page.locator('#chat-composer .p-goal');
  await target.waitFor();                                         // shown: the harness supports goals
  assert.equal(await target.getAttribute('title'), 'Goal');
  assert.equal(await page.locator('#chat-goal').isHidden(), true, 'no goal, no bar');

  // Set a goal from the target.
  await target.click();
  const form = page.locator('#chat-goal textarea');
  assert.equal(await form.getAttribute('placeholder'), 'What should it get done?');
  await form.fill(LONG); await form.press('Enter');
  await page.locator('#chat-goal .cg-bar').waitFor();
  assert.deepEqual(api.goalPosts[0], {action: 'set', objective: LONG});
  assert.equal(await page.locator('#chat-goal .cg-chip').innerText(), 'Working');
  assert.match(await target.getAttribute('class'), /\bon\b/, 'the target is filled');
  await page.locator('#side .tree-goal').waitFor();               // the bot's row has the mark

  // Three lines, then tap to read it all with Edit, Pause and Clear.
  const shut = await clampOf(page);
  assert.equal(shut.clamp, '3'); assert.equal(shut.lines, 3); assert.equal(shut.cut, true, 'a long goal is cut at three lines');
  await page.locator('#chat-goal .cg-bar').click();
  assert.equal((await clampOf(page)).cut, false, 'open shows the whole goal');
  assert.deepEqual(await page.locator('#chat-goal .cg-actions button').allInnerTexts(), ['Edit', 'Pause', 'Clear']);

  await page.getByRole('button', {name: 'Pause'}).click();
  await page.locator('#chat-goal .cg-chip', {hasText: 'Paused'}).waitFor();
  assert.equal(api.goalPosts.at(-1).action, 'pause');
  await page.getByRole('button', {name: 'Resume'}).click();
  await page.locator('#chat-goal .cg-chip', {hasText: 'Working'}).waitFor();
  await page.getByRole('button', {name: 'Clear'}).click();
  await page.locator('#chat-goal').waitFor({state: 'hidden'});
  assert.equal(api.goalPosts.at(-1).action, 'clear');
  assert.doesNotMatch(await target.getAttribute('class'), /\bon\b/);
  assert.equal(await page.locator('#side .tree-goal').count(), 0);

  // The "/" menu: filter, arrows, Return picks; a harness command goes out flagged.
  const box = page.locator('#chat-composer textarea');
  await box.click(); await box.pressSequentially('/');
  const menu = page.locator('#chat-composer .slash-menu');
  await menu.waitFor();
  assert.deepEqual(await menu.locator('.slash-name').allInnerTexts(), ['/goal', '/new', '/task', '/help', '/compact', '/model']);
  await box.pressSequentially('m');
  assert.deepEqual(await menu.locator('.slash-name').allInnerTexts(), ['/model', '/compact']);
  await box.press('ArrowDown'); await box.press('Enter');
  assert.equal(await box.inputValue(), '/compact');
  assert.equal(await menu.isHidden(), true);
  await box.press('Enter');
  await page.waitForFunction(() => !document.querySelector('#chat-composer textarea').value);
  assert.deepEqual(api.sends.at(-1), {text: '/compact', refs: {}, command: true});

  // Esc closes it; an unknown command is ordinary text.
  await box.pressSequentially('/frob');
  await page.waitForTimeout(50);
  assert.equal(await menu.isHidden(), true, 'nothing matches');
  await box.pressSequentially(' the widget'); await box.press('Enter');
  await page.waitForFunction(() => !document.querySelector('#chat-composer textarea').value);
  assert.deepEqual(api.sends.at(-1), {text: '/frob the widget', refs: {}});
  await box.pressSequentially('/'); await menu.waitFor();
  await box.press('Escape');
  assert.equal(await menu.isHidden(), true);
  await box.fill('');

  // /goal <text> sets it; /goal pa + Tab picks a sub-command.
  await box.pressSequentially('/goal Clean up the Acme wiki'); await box.press('Enter');
  await page.locator('#chat-goal .cg-bar').waitFor();
  assert.deepEqual(api.goalPosts.at(-1), {action: 'set', objective: 'Clean up the Acme wiki'});
  await box.pressSequentially('/goal pa');
  assert.deepEqual(await menu.locator('.slash-name').allInnerTexts(), ['/goal pause']);
  await box.press('Tab'); assert.equal(await box.inputValue(), '/goal pause');
  await box.press('Enter');
  await page.locator('#chat-goal .cg-chip', {hasText: 'Paused'}).waitFor();

  // The stream says it was met: the bar folds into one line in the thread.
  api.streamGoal = {...api.goal, status: 'met', note: 'All pages cleaned up', updated_at: new Date(Date.now() + 1000).toISOString(), ended_at: new Date().toISOString()};
  await page.locator('#conv-thread .chat-goal-line').waitFor({timeout: 5000});
  assert.match(await page.locator('#conv-thread .chat-goal-line').innerText(), /Goal met:\s*Clean up the Acme wiki\s*· All pages cleaned up/);
  assert.equal(await page.locator('#chat-goal').isHidden(), true);
  assert.deepEqual(api.errors, []);
  console.log('desktop: ok');
  await page.close();
}

async function phone(browser) {
  const api = await open(browser, {width: 390, height: 844}, true), {page} = api;
  api.goal = null;
  const target = page.locator('#chat-composer .p-goal');
  await target.waitFor();
  const box = page.locator('#chat-composer textarea');
  // Tap a row in the "/" menu.
  await box.tap(); await box.pressSequentially('/go');
  await page.locator('#chat-composer .slash-row', {hasText: '/goal'}).first().tap();
  assert.equal(await box.inputValue(), '/goal ');
  await box.pressSequentially(LONG.slice(0, 200));
  await page.locator('#chat-composer .p-send').tap();
  await page.locator('#chat-goal .cg-bar').waitFor();
  const shut = await clampOf(page);
  assert.equal(shut.clamp, '2'); assert.equal(shut.lines, 2);
  const bar = await page.locator('#chat-goal').boundingBox(), composer = await page.locator('#chat-composer').boundingBox();
  assert.ok(bar.y < composer.y && bar.height < 80, 'a slim bar above the chat');
  assert.ok(bar.x >= 0 && bar.x + bar.width <= 390, 'inside the screen');
  await page.locator('#chat-goal .cg-bar').tap();
  await page.locator('#chat-goal .cg-actions').waitFor();
  assert.deepEqual(api.errors, []);
  console.log('phone: ok');
  await page.close();
}

// LOCAL-1: the server's notice for a met goal (refs.chat_goal, refs.goal_status) is the compact goal line, not a "Task"
// line, and the /goal messages the goal controls sent are not bubbles.
async function serverNotice(browser) {
  const t = (m, s) => `2026-10-01T09:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}Z`;
  const messages = [
    {id: 'm1', kind: 'say', from_actor: 'human:ana', body: '/goal Produce the revised Acme summary', created: t(1, 0),
     refs: {command: true, goal_action: 'set', goal_id: 'g9', goal_revision: t(1, 0), goal_objective: 'Produce the revised Acme summary'}},
    {id: 'm2', kind: 'say', from_actor: 'human:ana', body: '/goal clear', created: t(2, 0), refs: {command: true, goal_action: 'pause', goal_id: 'g9'}},
    {id: 'm3', kind: 'say', from_actor: 'human:ana', body: '/goal Produce the revised Acme summary', created: t(3, 0), refs: {command: true, goal_action: 'resume', goal_id: 'g9'}},
    {id: 'm4', kind: 'say', from_actor: 'bot:ops', body: 'Summary revised and posted.', created: t(9, 0), refs: {}},
    // As backend/chat_goals.py writes it: refs {chat_goal, goal_status}; a two-line objective, then the note.
    {id: 'm5', kind: 'notice', from_actor: 'bot:ops', body: 'Goal met: Produce the revised Acme summary\nwith the Q3 numbers\nThe condition holds.', created: t(9, 5),
     refs: {chat_goal: 'g9', goal_status: 'met'}},
  ];
  const goal = {id: 'g9', conversation_id: 'c1', bot: 'ops', objective: 'Produce the revised Acme summary\nwith the Q3 numbers', status: 'met',
    note: 'The condition holds.', set_at: t(1, 0), updated_at: t(9, 5), ended_at: t(9, 5)};
  for (const [viewport, touch, name] of [[{width: 1280, height: 860}, false, 'desktop'], [{width: 390, height: 844}, true, 'phone']]) {
    const api = await open(browser, viewport, touch, {messages, goal, theme: 'dark'}), {page} = api;
    await page.locator('#conv-thread .chat-goal-line').waitFor();
    const thread = page.locator('#conv-thread');
    assert.equal(await thread.locator('.chat-goal-line').count(), 1, 'one line, not the notice and a line');
    // The objective's second line is not taken for the note.
    assert.match(await thread.locator('.chat-goal-line').innerText(), /Goal met:\s*Produce the revised Acme summary\s+with the Q3 numbers\s*· The condition holds\./);
    assert.doesNotMatch(await thread.innerText(), /\bTask\b/);
    assert.doesNotMatch(await thread.innerText(), /\/goal/, 'no raw /goal bubbles');
    assert.equal(await thread.locator('.bubble').count(), 1, 'only the bot\'s reply is a bubble');
    assert.equal(await page.locator('#chat-goal').isHidden(), true, 'an ended goal has no bar');
    const box = await thread.locator('.chat-goal-line').boundingBox();
    assert.ok(box.height < 40 && box.x >= 0 && box.x + box.width <= viewport.width, `${name}: one compact line`);
    if (process.env.CHAT_GOAL_SHOTS) {
      fs.mkdirSync(process.env.CHAT_GOAL_SHOTS, {recursive: true});
      await page.screenshot({path: require('node:path').join(process.env.CHAT_GOAL_SHOTS, `chat-goal-met-${name}-dark.png`)});
    }
    assert.deepEqual(api.errors, []);
    await page.close();
  }
  console.log('server notice: ok');
}

// LOCAL-3: an empty chat with a Codex bot offers the goal; the first /goal makes the room, then sets the goal there.
async function emptyChat(browser) {
  const api = await open(browser, {width: 1280, height: 860}, false, {empty: true}), {page} = api;
  const target = page.locator('#chat-composer .p-goal');
  await target.waitFor();
  const box = page.locator('#chat-composer textarea');
  await box.click(); await box.pressSequentially('/');
  const menu = page.locator('#chat-composer .slash-menu');
  await menu.waitFor();
  assert.ok((await menu.locator('.slash-name').allInnerTexts()).includes('/goal'), '/goal is offered before the first message');
  await box.fill('');
  await box.pressSequentially('/goal First thing I want done');
  await box.press('Escape');                                      // the menu, if open
  await box.press('Enter');
  await page.locator('#chat-goal .cg-bar').waitFor();
  assert.deepEqual(api.created, [{participants: ['bot:ops'], kind: 'chat'}]);
  assert.deepEqual(api.goalPosts, [{action: 'set', objective: 'First thing I want done'}]);
  assert.deepEqual(api.sends, [], 'the goal is not sent as plain text');
  assert.equal(await page.locator('#chat-goal .cg-text').innerText(), 'First thing I want done');
  await page.waitForFunction(() => !document.querySelector('#chat-composer textarea').value);
  assert.deepEqual(api.errors, []);
  await page.close();
  // The target works the same way, and Return twice sets it once.
  const again = await open(browser, {width: 1280, height: 860}, false, {empty: true});
  again.slowGoal = 300;
  await again.page.locator('#chat-composer .p-goal').click();
  const form = again.page.locator('#chat-goal textarea');
  await form.fill('Clean up the Acme wiki'); await form.press('Enter'); await form.press('Enter').catch(() => {});
  await again.page.locator('#chat-goal .cg-bar').waitFor();
  assert.equal(again.created.length, 1);
  assert.deepEqual(again.goalPosts, [{action: 'set', objective: 'Clean up the Acme wiki'}]);
  assert.deepEqual(again.errors, []);
  await again.page.close();

  // Its computer said goals do not work there: nothing is offered.
  const no = await open(browser, {width: 1280, height: 860}, false, {empty: true, readiness: {ready: true, goals: false}});
  await no.page.waitForTimeout(400);
  assert.equal(await no.page.locator('#chat-composer .p-goal').isHidden(), true);
  await no.page.close();

  // It said nothing, so the goal is offered, and the server refuses it: the typed goal stays with the reason.
  const refused = await open(browser, {width: 1280, height: 860}, false, {empty: true, supported: false, readiness: {ready: true}});
  await refused.page.locator('#chat-composer .p-goal').click();
  const box2 = refused.page.locator('#chat-goal textarea');
  await box2.fill('Ship the Acme pricing page');
  await refused.page.locator('#chat-goal [type=submit]').click();
  await refused.page.locator('#chat-goal .cg-why', {hasText: "doesn't support goals"}).waitFor();
  assert.equal(await box2.inputValue(), 'Ship the Acme pricing page', 'the typed goal is kept');
  assert.equal(refused.created.length, 1, "the bot's own room, the one its first message will use");
  assert.deepEqual(refused.goalPosts, [], 'not set where it is refused');
  assert.equal(await refused.page.locator('#chat-goal [type=submit]').isDisabled(), false);
  await refused.page.locator('#chat-goal [data-goal="cancel"]').click();
  await refused.page.locator('#chat-goal').waitFor({state: 'hidden'});
  assert.equal(await refused.page.locator('#chat-composer .p-goal').isHidden(), true, 'no longer offered');
  assert.deepEqual(refused.errors, []);
  await refused.page.close();
  console.log('empty chat: ok');
}

(async () => {
  const browser = await launch();
  try { await desktop(browser); await phone(browser); await serverNotice(browser); await emptyChat(browser); }
  finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
