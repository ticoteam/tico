// A bot's Home and DM keep the bot chat's composer (ui/app/thread-view.js, ui/app/chat-goal.js): one line (Attach, the
// words, Goal, Send) in one frame, with no question-card outline around it, on a phone and a computer, dark and light.
// Goal mode is back where the bot takes goals: the target toggles it (saved per bot), Send sets the text as the
// thread's goal through the goal route (POST /api/v2/conversations/<thread>/goal, never a message), the pinned goal
// shows in its bar with Edit, Pause and Clear, and the refusals are the old chat's. An empty DM's first goal makes the
// DM first, as its first message would. The /goal messages are not shown; a met goal is its one line. Fixtures only.
// TICO_SCREENSHOT_DIR=<dir> also saves the screenshots.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const {threadsFake} = require('./support/threads-fake.cjs');
const launch = () => chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
const shots = process.env.TICO_SCREENSHOT_DIR;
const BOT = 'engineering-manager', HOME = `t-home-${BOT}`, DM = `t-dm-${BOT}`;

async function open(browser, {width = 390, theme = 'dark', tab = 'home', supported = true, goalMode = false, dmMade = true, home = [], refuse = false} = {}) {
  const phone = width < 761;
  const page = await browser.newPage({viewport: {width, height: 844}, ...(phone ? {isMobile: true, hasTouch: true} : {}), serviceWorkers: 'block', reducedMotion: 'reduce'});
  await page.addInitScript(([t, slug, on]) => {
    try { localStorage.setItem('tico.theme', t); if (on) localStorage.setItem('hub.chat.goal-mode', JSON.stringify({[slug]: true})); } catch {}
  }, [theme, BOT, goalMode]);
  const api = {errors: [], sent: [], goalPosts: [], goalReads: [], dmMade: [], goals: {}, page, dm: dmMade};
  page.on('pageerror', e => api.errors.push(e.message));
  const fake = threadsFake({bot: BOT, home, sent: api.sent});
  const now = () => new Date().toISOString();
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname, method = req.method();
    const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (url.origin !== 'https://tico-ui.test') return route.abort();
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1]))) return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
    if (p.startsWith('/vendor/fonts/') && fs.existsSync(uiFile(p.slice(1)))) return route.fulfill({contentType: 'font/woff2', body: fs.readFileSync(uiFile(p.slice(1)))});
    if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    if (p === '/api/me') return json({id: 'ana', name: 'Ana', role: 'owner', cloud: true});
    if (p === '/api/employees') return json([{name: BOT, display_name: 'Engineering Manager', host: 'keeper', status: 'active', can_chat: true, schedules: [],
      resolved_runtime: 'codex', machine: {runner_id: 'r1'}, readiness: {ready: true, goals: supported}, my_access: {see: true, read: true, write: true}}]);
    if (p === '/api/issues') return json([]);
    if (p === '/api/v2/events' || p.endsWith('/watch')) return route.abort();
    // An empty DM: none until the first message or goal makes it (POST /v2/threads/dm with no body).
    if (p === '/api/v2/threads/dm' && method === 'GET' && !api.dm) return json({error: {code: 'no_dm', detail: 'No DM yet'}}, 404);
    if (p === '/api/v2/threads/dm' && method === 'POST' && !req.postDataJSON()?.body) { api.dmMade.push(req.postDataJSON()); api.dm = true; }
    const goal = p.match(/^\/api\/v2\/conversations\/([^/]+)\/goal$/);
    if (goal) {
      const tid = goal[1];
      if (method === 'GET') { api.goalReads.push(tid); return json({goal: api.goals[tid] || null, supported: supported && !refuse, commands: []}); }
      const body = req.postDataJSON();
      api.goalPosts.push({tid, ...body});
      if (!supported || refuse) return json({error: {code: 'goal_unsupported', detail: "This bot's harness doesn't support goals"}}, 409);
      const was = api.goals[tid];
      const status = {set: 'active', edit: was?.status || 'active', pause: 'paused', resume: 'active', clear: 'cleared'}[body.action];
      const g = {id: 'g1', conversation_id: tid, bot: BOT, objective: body.objective || was?.objective, status, note: '', set_by: 'human:ana',
                 set_at: was?.set_at || now(), updated_at: now(), ended_at: status === 'cleared' ? now() : null};
      api.goals[tid] = status === 'cleared' ? null : g;
      return json({goal: g});
    }
    const answer = fake(p, url, method, method === 'POST' ? req.postDataJSON() : null);
    if (answer !== undefined) return json(answer);
    return json(unrouted(p));
  });
  await page.goto(`https://tico-ui.test/#/bot/${BOT}/${tab}`);
  await page.locator('#bot-thread .thv-composer .p-text').waitFor();
  await page.waitForFunction(() => BOT?.thread?.view?.state !== 'loading');
  return api;
}
async function shot(page, name) {
  if (!shots) return;
  fs.mkdirSync(shots, {recursive: true});
  await page.screenshot({path: path.join(shots, `thread-composer-${name}.png`)});
}
// The composer's row: every control's middle on the text's line, the box one line tall, one frame.
const layout = page => page.locator('#bot-thread .thv-composer').evaluate(host => {
  const rect = el => el && !el.hidden && el.getClientRects().length ? el.getBoundingClientRect() : null;
  const mid = el => { const r = rect(el); return r ? Math.round(r.top + r.height / 2) : null; };
  const ask = host.querySelector('.ask'), pill = host.querySelector('.p-pill'), cs = getComputedStyle(ask), ps = getComputedStyle(pill);
  const order = ['.p-attach', '.p-text', '.p-goal', '.p-send'].map(s => host.querySelector(s)).filter(el => rect(el)).sort((a, b) => rect(a).left - rect(b).left)
    .map(el => el.className.match(/p-(attach|text|goal|send)\b/)[1]);
  return {pill: rect(pill).height, askBorder: [cs.borderTopWidth, cs.borderTopStyle], askPadding: cs.paddingTop, pillBorder: ps.borderTopWidth,
    pillColor: ps.borderTopColor, ink: getComputedStyle(document.body).getPropertyValue('--line').trim(),
    text: mid(host.querySelector('.p-text')), attach: mid(host.querySelector('.p-attach')), goal: mid(host.querySelector('.p-goal')), send: mid(host.querySelector('.p-send')),
    order, right: rect(pill).right, left: rect(pill).left, width: innerWidth};
});

async function looks(browser) {
  for (const [width, theme] of [[390, 'dark'], [390, 'light'], [1280, 'dark'], [1280, 'light']]) {
    const api = await open(browser, {width, theme}), {page} = api;
    await page.locator('#bot-thread .p-goal:not([hidden])').waitFor();
    const l = await layout(page), name = `${width}-${theme}`;
    assert.ok(l.pill < 60, `${name}: one line tall when empty (${l.pill}px)`);
    for (const k of ['attach', 'goal', 'send']) assert.ok(Math.abs(l[k] - l.text) <= 3, `${name}: ${k} on the text's line (${JSON.stringify(l)})`);
    assert.deepEqual(l.askBorder, ['0px', 'none'], `${name}: no outline around the pill (the task question card's amber frame)`);
    assert.equal(l.askPadding, '0px', `${name}: no second box around the pill`);
    assert.equal(l.pillBorder, '1px', `${name}: one frame`);
    assert.deepEqual(l.order, width < 761 ? ['attach', 'text', 'goal', 'send'] : ['text', 'goal', 'attach', 'send'], `${name}: the old bar's order`);
    assert.ok(l.left >= 0 && l.right <= l.width, `${name}: inside the screen`);
    await shot(page, `home-${width < 761 ? 'phone' : 'desktop'}-${theme}`);
    // A long message grows the box; emptied, it is one line again.
    await page.locator('#bot-thread .p-text').fill('A longer message that wraps onto a second line of the box on a phone and keeps going on a computer too. '.repeat(width < 761 ? 1 : 2));
    await page.locator('#bot-thread .p-text').dispatchEvent('input');
    assert.ok((await layout(page)).pill > l.pill, `${name}: grows when the words wrap`);
    await page.locator('#bot-thread .p-text').fill('');
    await page.locator('#bot-thread .p-text').dispatchEvent('input');
    assert.equal((await layout(page)).pill, l.pill, `${name}: one line again`);
    assert.deepEqual(api.errors, []);
    await page.close();
  }
}

async function goalMode(browser) {
  const api = await open(browser, {width: 1280}), {page} = api;
  const target = page.locator('#bot-thread .p-goal'), send = page.locator('#bot-thread .p-send'), box = page.locator('#bot-thread .p-text');
  await target.waitFor();
  assert.equal(await target.getAttribute('aria-pressed'), 'false');
  assert.equal(await send.getAttribute('aria-label'), 'Send');
  assert.ok(api.goalReads.includes(HOME), 'the Home asks the goal route about its own thread');
  // The target turns goal mode on, saved for this bot; Send says so in words.
  await target.click();
  assert.equal(await target.getAttribute('aria-pressed'), 'true');
  assert.equal(await send.innerText(), 'Set goal');
  assert.equal(await box.getAttribute('placeholder'), 'Goal for Engineering Manager…');
  assert.deepEqual(await page.evaluate(() => JSON.parse(localStorage.getItem('hub.chat.goal-mode'))), {[BOT]: true});
  await shot(page, 'goal-mode-desktop-dark');
  // Send sets the goal of this thread, through the goal route, and posts no message.
  await box.fill('Ship the Acme pricing page');
  await box.press('Enter');
  await page.locator('#bot-thread .thv-goal .cg-text').waitFor();
  assert.deepEqual(api.goalPosts, [{tid: HOME, action: 'set', objective: 'Ship the Acme pricing page'}]);
  assert.deepEqual(api.sent, [], 'goal mode sends no message');
  assert.equal(await box.inputValue(), '');
  assert.equal(await page.locator('#bot-thread .thv-goal .cg-chip').innerText(), 'Working');
  assert.equal(await target.isHidden(), true, 'a pinned goal takes the target out of the composer');
  assert.equal(await send.getAttribute('aria-label'), 'Send', 'the composer is back to chatting');
  // The bar opens to Edit, Pause and Clear; Clear brings the target back, with goal mode off.
  await page.locator('#bot-thread .thv-goal .cg-bar').click();
  assert.deepEqual(await page.locator('#bot-thread .thv-goal .cg-actions button').allInnerTexts(), ['Edit', 'Pause', 'Clear']);
  await page.locator('#bot-thread .thv-goal [data-goal="pause"]').click();
  await page.waitForFunction(() => document.querySelector('#bot-thread .thv-goal .cg-chip')?.textContent === 'Paused');
  await page.locator('#bot-thread .thv-goal [data-goal="clear"]').click();
  await page.locator('#bot-thread .thv-goal').waitFor({state: 'hidden'});
  assert.deepEqual(api.goalPosts.map(g => g.action), ['set', 'pause', 'clear']);
  assert.equal(await target.isHidden(), false);
  assert.equal(await target.getAttribute('aria-pressed'), 'false');
  // A file always goes as a message: goal mode waits while one is attached. Off again, Send is a message.
  await target.click();
  await box.fill('Plain words');
  await target.click();
  await send.click();
  await page.waitForFunction(() => !document.querySelector('#bot-thread .th-pending'));
  assert.deepEqual(api.sent.map(b => b.text), ['Plain words']);
  assert.equal(api.goalPosts.length, 3);
  assert.deepEqual(api.errors, []);
  await page.close();
}

// An empty DM has no thread: its first goal makes the DM (as its first message would), then sets it there.
async function emptyDm(browser) {
  const api = await open(browser, {width: 390, tab: 'dm', dmMade: false, goalMode: true}), {page} = api;
  const target = page.locator('#bot-thread .p-goal'), box = page.locator('#bot-thread .p-text');
  await target.waitFor();
  assert.equal(await target.getAttribute('aria-pressed'), 'true', 'goal mode is kept per bot');
  assert.equal(await page.locator('#bot-thread .p-send').innerText(), 'Set goal');
  const l = await layout(page);
  assert.ok(l.pill < 60 && Math.abs(l.send - l.text) <= 3, `"Set goal" keeps one line on a phone (${JSON.stringify(l)})`);
  await shot(page, 'goal-mode-phone-dark');
  await box.fill('First thing I want done');
  await page.locator('#bot-thread .p-send').click();
  await page.locator('#bot-thread .thv-goal .cg-text').waitFor();
  assert.deepEqual(api.dmMade, [{members: [`bot:${BOT}`]}], 'the DM, made with no message');
  assert.deepEqual(api.goalPosts, [{tid: DM, action: 'set', objective: 'First thing I want done'}]);
  assert.deepEqual(api.sent.filter(b => b?.text || b?.body), [], 'not sent as a message');
  assert.equal(await page.locator('#bot-thread .thv-goal .cg-text').innerText(), 'First thing I want done');
  assert.deepEqual(api.errors, []);
  await page.close();
}

// Where goals don't work: no target; goal mode left on from before keeps its target (to turn it off) and refuses to
// send the words anywhere. A server that refuses the goal says why and keeps the words.
async function refusals(browser) {
  let api = await open(browser, {width: 1280, supported: false}), {page} = api;
  await page.waitForFunction(() => BOT?.thread?.view?.goals?.conv);
  await page.waitForTimeout(100);
  assert.equal(await page.locator('#bot-thread .p-goal').isHidden(), true, 'no target where the bot takes no goals');
  await page.close();

  ({page} = api = await open(browser, {width: 1280, supported: false, goalMode: true}));
  const target = page.locator('#bot-thread .p-goal'), box = page.locator('#bot-thread .p-text');
  await target.waitFor();
  assert.equal(await target.getAttribute('aria-pressed'), 'true');
  await box.fill('Ship it');
  await box.press('Enter');
  await page.locator('.toast').filter({hasText: "Goal mode isn't available for this chat"}).waitFor();
  assert.deepEqual([api.goalPosts, api.sent], [[], []]);
  assert.equal(await box.inputValue(), 'Ship it', 'the words stay');
  await target.click();
  assert.equal(await target.isHidden(), true, 'off, the target goes');
  await page.close();

  ({page} = api = await open(browser, {width: 1280, tab: 'dm', dmMade: false, goalMode: true, refuse: true}));
  await page.locator('#bot-thread .p-goal').waitFor();
  await page.locator('#bot-thread .p-text').fill('Ship the Acme pricing page');
  await page.locator('#bot-thread .p-send').click();
  await page.locator('.toast').filter({hasText: "This bot's harness doesn't support goals."}).waitFor();
  assert.deepEqual(api.goalPosts, [], 'not set where the server refuses it');
  assert.equal(await page.locator('#bot-thread .p-text').inputValue(), 'Ship the Acme pricing page');
  assert.deepEqual(api.errors, []);
  await page.close();
}

// The /goal messages the controls sent are not bubbles; the server's notice for a met goal is the goal's one line.
async function lines(browser) {
  const t = m => `2026-10-01T09:0${m}:00Z`;
  const home = [
    {id: 'm1', sender_id: 'human:ana', body: '/goal Produce the Acme summary', created_at: t(1),
     refs: {command: true, goal_action: 'set', goal_id: 'g9', goal_objective: 'Produce the Acme summary'}},
    {id: 'm2', sender_id: `bot:${BOT}`, body: 'Summary posted.', created_at: t(2), refs: {}},
    {id: 'm3', kind: 'notice', sender_id: `bot:${BOT}`, body: 'Goal met: Produce the Acme summary\nThe condition holds.', created_at: t(3),
     refs: {chat_goal: 'g9', goal_status: 'met'}},
  ];
  const api = await open(browser, {width: 1280, home}), {page} = api;
  const list = page.locator('#bot-thread .thv-list');
  await list.locator('.chat-goal-line').waitFor();
  assert.match(await list.locator('.chat-goal-line').innerText(), /Goal met:\s*Produce the Acme summary\s*· The condition holds\./);
  assert.doesNotMatch(await list.innerText(), /\/goal/, 'no raw /goal messages');
  assert.equal(await list.locator('.th-msg').count(), 2);
  assert.deepEqual(api.errors, []);
  await page.close();
}

(async () => {
  const browser = await launch();
  try {
    await looks(browser);
    await goalMode(browser);
    await emptyDm(browser);
    await refusals(browser);
    await lines(browser);
    console.log('thread composer: one line, one frame, goal mode on Home and DM, refusals, goal lines: ok');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
