// A bot's Home and DM keep the bot chat's composer (ui/app/thread-view.js, ui/app/chat-goal.js): one line (Attach, the
// words, the ways caret, Send) in one frame, with no question-card outline around it, on a phone and a computer, dark
// and light. Send shows the person's kept way (Steer, Queue, Defer; pill.js) and every message names it; the caret, a
// right-click or a long press opens the ways, and a pick sticks across a reload. Set as goal is in that menu, for one
// goal: a Goal chip, then Send sets the text as the thread's goal through the goal route
// (POST /api/v2/conversations/<thread>/goal, never a message); the pinned goal shows in its bar with Edit, Pause and
// Clear, and the refusals are the old chat's. An empty DM's first goal makes the DM first, as its first message would.
// A person-to-person thread keeps a plain Send. The /goal messages are not shown; a met goal is its one line. The old bar's
// other pieces are back too: on a phone the bot's avatar at the left edge opens the bot switcher (ui/app/org-fan.js);
// Stop takes Send's place while the bot runs (read from the thread's conversation snapshot) and posts the stop; "/"
// opens the commands menu, and a command does what it did in the old chat (ui/app/slash-commands.js). Fixtures only.
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

const COMMANDS = [
  {name: 'goal', args: '<objective>', help: 'Pin a goal the bot works toward', kind: 'tico', sub: ['pause', 'resume', 'clear', 'edit']},
  {name: 'new', args: '', help: 'New chat', kind: 'tico'}, {name: 'task', args: '<title>', help: 'Make a task for this bot', kind: 'tico'},
  {name: 'branch', args: '', help: 'Make my branch', kind: 'tico'}, {name: 'help', args: '', help: 'Show commands', kind: 'tico'},
  {name: 'compact', args: '', help: 'Compact this conversation', kind: 'harness'}];

async function open(browser, {width = 390, theme = 'dark', tab = 'home', supported = true, goalMode = false, dmMade = true, home = [], refuse = false,
                              execution = null, commands = [], role = 'owner', recent = [], write = true, prefs = {}} = {}) {
  const phone = width < 761;
  const page = await browser.newPage({viewport: {width, height: 844}, ...(phone ? {isMobile: true, hasTouch: true} : {}), serviceWorkers: 'block', reducedMotion: 'reduce'});
  await page.addInitScript(([t, slug, on, recent]) => {
    try {
      localStorage.setItem('tico.theme', t); if (on) localStorage.setItem('hub.chat.goal-mode', JSON.stringify({[slug]: true}));
      localStorage.setItem('tico.org.recent', JSON.stringify(recent));
    } catch {}
  }, [theme, BOT, goalMode, recent]);
  const api = {errors: [], sent: [], goalPosts: [], goalReads: [], dmMade: [], goals: {}, page, dm: dmMade, execution,
               stops: [], commands: [], tasks: [], patches: [], snapshots: [], prefs: {...prefs}, slowRead: false, uploads: []};
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
    if (p === '/api/me') return json({id: 'ana', name: 'Ana', role, cloud: true});
    const bot = (name, display_name) => ({name, display_name, host: 'keeper', status: 'active', can_chat: true, schedules: [],
      resolved_runtime: 'codex', machine: {runner_id: 'r1'}, readiness: {ready: true, goals: supported}, my_access: {see: true, read: true, write}});
    if (p === '/api/employees') return json([bot(BOT, 'Engineering Manager'), bot('designer', 'Designer'), bot('sales-lead', 'Sales Lead')]);
    // The thread's newest run (Stop), the stop itself, a harness command, a task and an archived DM (/new).
    const snap = p.match(/^\/api\/v2\/conversations\/([^/]+)\/snapshot$/);
    if (snap) { api.snapshots.push(snap[1]); return json({messages: [], next_before: null, execution: api.execution, goal: null}); }
    if (p === `/api/v2/bots/${BOT}/stop`) { api.stops.push(req.postDataJSON()); return json({attempt_id: 'a1', stopping: true}); }
    if (p === '/api/v2/messages' && method === 'POST') { api.commands.push(req.postDataJSON()); return json({id: 'cmd-1', conversation_id: req.postDataJSON().conversation_id}); }
    if (p === '/api/v2/tasks' && method === 'POST') { api.tasks.push(req.postDataJSON()); return json({task: {id: 'task-1', title: req.postDataJSON().title}}); }
    if (method === 'PATCH' && p.startsWith('/api/v2/threads/')) { api.patches.push({p, ...req.postDataJSON()}); api.dm = false; return json({id: p.split('/')[4], archived_at: now()}); }
    if (p === '/api/issues') return json([]);
    // A message with files: the form's fields, as text.
    const upload = p.match(/^\/api\/v2\/uploads\/threads\/([^/]+)\/messages$/);
    if (upload && method === 'POST') {
      api.uploads.push(req.postData() || '');
      return json({message: {id: 'up-' + api.uploads.length, seq: 2000 + api.uploads.length, depth: 0, thread_id: upload[1], sender_id: 'human:ana', body: '', created_at: now()}});
    }
    // The kept way to send (compose.send_way); a slow read answers with what the server held when it came in.
    const pref = p.match(/^\/api\/v2\/preferences\/([^/]+)$/);
    if (pref && method === 'POST') { api.prefs[pref[1]] = req.postDataJSON().value; return json({key: pref[1], value: api.prefs[pref[1]]}); }
    if (pref) {
      const value = api.prefs[pref[1]] ?? null;
      if (api.slowRead) await new Promise(done => setTimeout(done, 1500));
      return json({key: pref[1], value});
    }
    if (p === '/api/v2/events' || p.endsWith('/watch')) return route.abort();
    // An empty DM: none until the first message or goal makes it (POST /v2/threads/dm with no body).
    if (p === '/api/v2/threads/dm' && method === 'GET' && !api.dm) return json({error: {code: 'no_dm', detail: 'No DM yet'}}, 404);
    if (p === '/api/v2/threads/dm' && method === 'POST' && !req.postDataJSON()?.body) { api.dmMade.push(req.postDataJSON()); api.dm = true; }
    const goal = p.match(/^\/api\/v2\/conversations\/([^/]+)\/goal$/);
    if (goal) {
      const tid = goal[1];
      if (method === 'GET') { api.goalReads.push(tid); return json({goal: api.goals[tid] || null, supported: supported && !refuse, commands}); }
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
  const order = ['.p-attach', '.p-text', '.p-way', '.p-send'].map(s => host.querySelector(s)).filter(el => rect(el)).sort((a, b) => rect(a).left - rect(b).left)
    .map(el => el.className.match(/p-(attach|text|way|send)\b/)[1]);
  return {pill: rect(pill).height, askBorder: [cs.borderTopWidth, cs.borderTopStyle], askPadding: cs.paddingTop, pillBorder: ps.borderTopWidth,
    pillColor: ps.borderTopColor, ink: getComputedStyle(document.body).getPropertyValue('--line').trim(),
    text: mid(host.querySelector('.p-text')), attach: mid(host.querySelector('.p-attach')), way: mid(host.querySelector('.p-way')), send: mid(host.querySelector('.p-send')),
    order, right: rect(pill).right, left: rect(pill).left, width: innerWidth};
});

async function looks(browser) {
  for (const [width, theme] of [[390, 'dark'], [390, 'light'], [1280, 'dark'], [1280, 'light']]) {
    const api = await open(browser, {width, theme}), {page} = api;
    await page.locator('#bot-thread .p-way:not([hidden])').waitFor();
    const l = await layout(page), name = `${width}-${theme}`;
    assert.ok(l.pill < 60, `${name}: one line tall when empty (${l.pill}px)`);
    for (const k of ['attach', 'way', 'send']) assert.ok(Math.abs(l[k] - l.text) <= 3, `${name}: ${k} on the text's line (${JSON.stringify(l)})`);
    assert.deepEqual(l.askBorder, ['0px', 'none'], `${name}: no outline around the pill (the task question card's amber frame)`);
    assert.equal(l.askPadding, '0px', `${name}: no second box around the pill`);
    assert.equal(l.pillBorder, '1px', `${name}: one frame`);
    assert.deepEqual(l.order, width < 761 ? ['attach', 'text', 'way', 'send'] : ['text', 'attach', 'way', 'send'], `${name}: the old bar's order`);
    assert.equal(await page.locator('#bot-thread .p-goal').count(), 0, `${name}: no target button: Set as goal is in the ways`);
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

// The ways menu of a bot's composer: open it, pick a way (a row, or one of Defer's durations), and see it close.
const caretOf = page => page.locator('#bot-thread .p-way'), menuOf = page => page.locator('#bot-thread .p-ways');
async function pick(page, way) {
  await caretOf(page).click();
  await menuOf(page).locator(`[data-deliver="${way}"]`).last().click();
  assert.equal(await menuOf(page).isHidden(), true, 'a pick closes the menu');
}
const shows = page => page.locator('#bot-thread .p-send').getAttribute('data-shows');

async function goalMode(browser) {
  const api = await open(browser, {width: 1280}), {page} = api;
  const chip = page.locator('#bot-thread .thv-goal-chip'), send = page.locator('#bot-thread .p-send'), box = page.locator('#bot-thread .p-text');
  await caretOf(page).waitFor();
  await page.waitForFunction(() => BOT?.thread?.view?.goals?.goalSupported);
  assert.equal(await send.getAttribute('aria-label'), 'Send');
  assert.equal(await chip.isHidden(), true);
  assert.ok(api.goalReads.includes(HOME), 'the Home asks the goal route about its own thread');
  // Set as goal: the Goal chip, the target on Send, and the box says so. Not kept for next time.
  await caretOf(page).click();
  assert.deepEqual(await menuOf(page).locator(':scope > button > span:nth-child(2)').allInnerTexts(), ['Steer now', 'Queue', 'Defer', 'Set as goal']);
  await menuOf(page).locator('[data-deliver="goal"]').click();
  assert.equal(await chip.isVisible(), true);
  assert.equal(await shows(page), 'goal:');
  assert.equal(await send.getAttribute('aria-label'), 'Set as goal');
  assert.equal(await box.getAttribute('placeholder'), 'Goal for Engineering Manager…');
  assert.equal(await page.evaluate(() => localStorage.getItem('hub.chat.goal-mode')), null, 'goal mode is never saved');
  await shot(page, 'goal-mode-desktop-dark');
  // Send sets the goal of this thread, through the goal route, and posts no message; then Send is the kept way again.
  await box.fill('Ship the Acme pricing page');
  await box.press('Enter');
  await page.locator('#bot-thread .thv-goal .cg-text').waitFor();
  assert.deepEqual(api.goalPosts, [{tid: HOME, action: 'set', objective: 'Ship the Acme pricing page'}]);
  assert.deepEqual(api.sent, [], 'goal mode sends no message');
  assert.equal(await box.inputValue(), '');
  assert.equal(await page.locator('#bot-thread .thv-goal .cg-chip').innerText(), 'Working');
  assert.equal(await chip.isHidden(), true, 'one goal, then out of goal mode');
  assert.equal(await shows(page), 'steer:');
  assert.equal(await send.getAttribute('aria-label'), 'Send', 'the composer is back to chatting');
  await caretOf(page).click();
  assert.equal(await menuOf(page).locator('[data-deliver="goal"]').count(), 0, 'a pinned goal is changed from its bar, not the menu');
  await caretOf(page).click();
  // The bar opens to Edit, Pause and Clear; Clear brings Set as goal back.
  await page.locator('#bot-thread .thv-goal .cg-bar').click();
  assert.deepEqual(await page.locator('#bot-thread .thv-goal .cg-actions button').allInnerTexts(), ['Edit', 'Pause', 'Clear']);
  await page.locator('#bot-thread .thv-goal [data-goal="pause"]').click();
  await page.waitForFunction(() => document.querySelector('#bot-thread .thv-goal .cg-chip')?.textContent === 'Paused');
  await page.locator('#bot-thread .thv-goal [data-goal="clear"]').click();
  await page.locator('#bot-thread .thv-goal').waitFor({state: 'hidden'});
  assert.deepEqual(api.goalPosts.map(g => g.action), ['set', 'pause', 'clear']);
  // The chip's × leaves goal mode; so does picking a way, which is kept.
  await pick(page, 'goal');
  await chip.locator('button').click();
  assert.equal(await chip.isHidden(), true);
  assert.equal(await shows(page), 'steer:');
  await pick(page, 'goal');
  await box.fill('Plain words');
  await pick(page, 'queue');
  assert.equal(await chip.isHidden(), true);
  await send.click();
  await page.waitForFunction(() => !document.querySelector('#bot-thread .th-pending'));
  assert.deepEqual(api.sent, [{text: 'Plain words', delivery: 'queue'}]);
  assert.equal(api.goalPosts.length, 3);
  await page.waitForFunction(() => !SEND_WAY_TIMER);
  assert.equal(api.prefs['compose.send_way'], 'queue', 'goal is never kept');
  // In goal mode a file goes as a message, the kept way: never a delivery of "goal".
  await pick(page, 'goal');
  await page.locator('#bot-thread .p-file').setInputFiles({name: 'brief.txt', mimeType: 'text/plain', buffer: Buffer.from('Pricing brief')});
  await box.fill('The brief');
  assert.equal(await shows(page), 'queue:', 'with a file attached Send is a message again');
  await send.click();
  await page.waitForFunction(() => !document.querySelector('#bot-thread .th-pending'));
  assert.equal(api.uploads.length, 1);
  const field = name => (api.uploads[0].match(new RegExp(`name="${name}"\\r\\n\\r\\n([^\\r]*)`)) || [])[1];
  assert.equal(field('delivery'), 'queue');
  assert.equal(field('text'), 'The brief');
  assert.equal(api.goalPosts.length, 3);
  assert.deepEqual(api.errors, []);
  await page.close();
}

// Send shows the kept way and every message names it: read back on load and after a reload, changed by a pick (a menu
// row or one of Defer's durations), opened from the caret, a right-click or a long press on Send. Someone who may not
// message the bot only defers, with no deadline. A person-to-person thread has a plain Send and no ways.
async function ways(browser) {
  let api = await open(browser, {width: 1280, theme: 'light', prefs: {'compose.send_way': 'defer:4h'}}), {page} = api;
  const send = page.locator('#bot-thread .p-send'), box = page.locator('#bot-thread .p-text');
  const say = async text => {
    const n = api.sent.length;
    await box.fill(text);
    await send.click();
    await page.waitForFunction(k => !document.querySelector('#bot-thread .th-pending') && document.querySelector('#bot-thread .p-text').value === '', n);
    assert.equal(api.sent.length, n + 1);
    return api.sent.at(-1);
  };
  await page.waitForFunction(() => document.querySelector('#bot-thread .p-send')?.dataset.shows === 'defer:4h');
  assert.equal(await send.locator('.p-way-badge').innerText(), '4h', "Defer's deadline rides on Send");
  assert.equal(await send.getAttribute('aria-label'), 'Send, defer 4h');
  assert.deepEqual(await say('Later today'), {text: 'Later today', delivery: 'defer', defer_for: '4h'});
  await caretOf(page).click();
  assert.equal(await caretOf(page).getAttribute('aria-expanded'), 'true');
  assert.equal(await menuOf(page).locator('[data-deliver="defer:4h"][aria-checked="true"]').count(), 2, 'the Defer row and its 4h chip');
  await shot(page, 'ways-desktop-light');
  await menuOf(page).locator('[data-deliver="steer"]').click();
  assert.equal(await shows(page), 'steer:');
  assert.equal(await send.locator('.p-way-badge').count(), 0);
  assert.deepEqual(await say('Now please'), {text: 'Now please', delivery: 'steer'});
  await pick(page, 'queue');
  assert.equal(await send.getAttribute('aria-label'), 'Send, queue');
  assert.deepEqual(await say('After this run'), {text: 'After this run', delivery: 'queue'});
  assert.equal(await shows(page), 'queue:', 'the pick sticks after a send');
  await page.waitForFunction(() => !SEND_WAY_TIMER);
  assert.equal(api.prefs['compose.send_way'], 'queue');

  // A right-click on Send opens the ways and sends nothing; Escape closes them and puts focus on the caret; a press
  // outside closes them too.
  await box.fill('Not yet');
  await send.click({button: 'right'});
  await menuOf(page).waitFor();
  assert.equal(api.sent.length, 3);
  await page.keyboard.press('Escape');
  assert.equal(await menuOf(page).isHidden(), true);
  assert.equal(await page.evaluate(() => document.activeElement?.classList.contains('p-way')), true);
  await caretOf(page).click();
  await page.locator('#bot-thread .thv-scroll').click({position: {x: 10, y: 10}});
  assert.equal(await menuOf(page).isHidden(), true);
  assert.equal(await box.inputValue(), 'Not yet');

  // A read of the kept way that answers after a pick does not undo the pick.
  api.prefs['compose.send_way'] = 'steer'; api.slowRead = true;
  await page.evaluate(async () => { const read = sendWaySync(); sendWayPick('defer:2d'); await read; });
  api.slowRead = false;
  assert.equal(await shows(page), 'defer:2d', 'a late read loses to the pick');
  await page.waitForFunction(() => !SEND_WAY_TIMER);
  assert.equal(api.prefs['compose.send_way'], 'defer:2d');
  // A reload reads the kept way back.
  await page.reload();
  await page.waitForFunction(() => document.querySelector('#bot-thread .p-send')?.dataset.shows === 'defer:2d');
  assert.equal(await page.locator('#bot-thread .thv-goal-chip').isHidden(), true);
  assert.deepEqual(api.errors, []);

  // A person-to-person thread: the same composer, a plain Send, no ways, and its messages name none.
  await page.evaluate(() => {
    const host = document.createElement('div');
    host.id = 'person-thread';
    Object.assign(host.style, {position: 'fixed', left: '20px', right: '20px', bottom: '20px', height: '300px', zIndex: '60', background: 'var(--bg)'});
    document.body.append(host);
    threadMount(host, {dm: 'human:ben', header: false});
  });
  const person = page.locator('#person-thread');
  await person.locator('.p-text').waitFor();
  assert.equal(await person.locator('.p-way, .p-sendbox, .p-ways, .thv-goal-chip').count(), 0, 'no caret with a person');
  assert.equal(await person.locator('.p-send').getAttribute('aria-label'), 'Send');
  assert.equal(await person.locator('.p-send').getAttribute('data-shows'), null, 'the plain arrow');
  await person.locator('.p-text').fill('Lunch at noon?');
  await person.locator('.p-send').click();
  await page.waitForFunction(() => !document.querySelector('#person-thread .th-pending') && !document.querySelector('#person-thread .p-text').value);
  assert.equal('delivery' in (api.sent.at(-1) || {}), false, 'no way named');
  assert.equal(api.sent.at(-1).text ?? api.sent.at(-1).body, 'Lunch at noon?');
  assert.deepEqual(api.errors, []);
  await page.close();

  // A long press on Send opens the ways on a phone; the touch that ends it sends nothing.
  ({page} = api = await open(browser, {width: 390, theme: 'dark'}));
  await caretOf(page).waitFor();
  await page.locator('#bot-thread .p-text').fill('Held down');
  const btn = page.locator('#bot-thread .p-send');
  await btn.dispatchEvent('pointerdown', {pointerType: 'touch', bubbles: true});
  await menuOf(page).waitFor();
  await btn.dispatchEvent('pointerup', {pointerType: 'touch', bubbles: true});
  await btn.dispatchEvent('click');
  assert.deepEqual(api.sent, [], 'the long press does not send');
  const fits = await menuOf(page).evaluate(m => { const r = m.getBoundingClientRect(); return r.left >= 0 && r.right <= innerWidth && r.top >= 0; });
  assert.ok(fits, 'the menu fits on a phone');
  await shot(page, 'ways-phone-dark');
  await page.close();

  // Someone who may not message the bot: Defer only, with no deadline, and Send defers.
  ({page} = api = await open(browser, {width: 1280, write: false}));
  await caretOf(page).waitFor();
  await page.waitForFunction(() => document.querySelector('#bot-thread .p-send')?.dataset.shows === 'defer:never');
  await caretOf(page).click();
  assert.deepEqual(await menuOf(page).locator(':scope > button:not([data-deliver="goal"]) > span:nth-child(2)').allInnerTexts(), ['Defer']);
  assert.equal(await menuOf(page).locator('.p-for').count(), 0, 'no deadlines');
  await caretOf(page).click();
  await page.locator('#bot-thread .p-text').fill('When you get to it');
  await page.locator('#bot-thread .p-send').click();
  await page.waitForFunction(() => !document.querySelector('#bot-thread .th-pending'));
  assert.deepEqual(api.sent, [{text: 'When you get to it', delivery: 'defer', defer_for: 'never'}]);
  assert.deepEqual(api.errors, []);
  await page.close();
}

// An empty DM has no thread: its first goal makes the DM (as its first message would), then sets it there.
async function emptyDm(browser) {
  const api = await open(browser, {width: 390, tab: 'dm', dmMade: false}), {page} = api;
  const box = page.locator('#bot-thread .p-text');
  await caretOf(page).waitFor();
  await pick(page, 'goal');
  assert.equal(await page.locator('#bot-thread .thv-goal-chip').isVisible(), true);
  const l = await layout(page);
  assert.ok(l.pill < 60 && Math.abs(l.send - l.text) <= 3, `the Goal chip keeps one line on a phone (${JSON.stringify(l)})`);
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

// Where goals don't work: no Set as goal. Goal mode left on by the old target does not come back: Send is a message.
// A server that refuses the goal says why and keeps the words.
async function refusals(browser) {
  let api = await open(browser, {width: 1280, supported: false, goalMode: true}), {page} = api;
  await page.waitForFunction(() => BOT?.thread?.view?.goals?.conv);
  await page.waitForTimeout(100);
  await caretOf(page).click();
  assert.equal(await menuOf(page).locator('[data-deliver="goal"]').count(), 0, 'no Set as goal where the bot takes no goals');
  await caretOf(page).click();
  assert.equal(await page.locator('#bot-thread .thv-goal-chip').isHidden(), true, 'a saved goal mode is not restored');
  await page.locator('#bot-thread .p-text').fill('Ship it');
  await page.locator('#bot-thread .p-text').press('Enter');
  await page.waitForFunction(() => !document.querySelector('#bot-thread .th-pending'));
  assert.deepEqual([api.goalPosts, api.sent], [[], [{text: 'Ship it', delivery: 'steer'}]]);
  await page.close();

  ({page} = api = await open(browser, {width: 1280, tab: 'dm', dmMade: false, refuse: true}));
  await caretOf(page).waitFor();
  await pick(page, 'goal');
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

// The bot's avatar: on a phone, at the bar's left edge before Attach, opening the switcher over the bar (recent bots,
// the menu, Search) with aria-expanded in step; on a computer there is none.
async function botSwitch(browser) {
  let api = await open(browser, {width: 390, recent: ['designer', 'sales-lead']}), {page} = api;
  const avatar = page.locator('#bot-thread .p-bot'), fan = page.locator('#org-fan');
  await avatar.waitFor();
  const at = await page.locator('#bot-thread .thv-composer').evaluate(host => {
    const x = s => host.querySelector(s).getBoundingClientRect();
    return {bot: x('.p-bot'), attach: x('.p-attach'), text: x('.p-text'), pill: x('.p-pill')};
  });
  assert.ok(at.bot.right <= at.attach.left && at.attach.right <= at.text.left, `avatar, then Attach, then the words (${JSON.stringify(at)})`);
  assert.ok(Math.abs((at.bot.top + at.bot.bottom) - (at.text.top + at.text.bottom)) <= 6 && at.pill.height < 60, 'one line');
  assert.equal(await avatar.getAttribute('aria-expanded'), 'false');
  await avatar.tap();
  await fan.locator('[data-org-bot="designer"]').waitFor();
  assert.equal(await avatar.getAttribute('aria-expanded'), 'true');
  assert.deepEqual(await fan.locator('[data-org-bot]').evaluateAll(els => els.map(e => e.dataset.orgBot)), ['designer', 'sales-lead'], 'the recent bots, not this one');
  assert.equal(await fan.locator('[data-org-search]').count(), 1);
  // It rises from the bar: the lowest bot sits above the avatar.
  const [low, bar] = [await fan.locator('[data-org-bot="designer"]').boundingBox(), await avatar.boundingBox()];
  assert.ok(low.y + low.height <= bar.y, 'above the bar');
  await shot(page, 'fan-phone-dark');
  await page.keyboard.press('Escape');
  await fan.waitFor({state: 'hidden'});
  assert.equal(await avatar.getAttribute('aria-expanded'), 'false');
  // A pick goes to that bot's page.
  await avatar.tap();
  await fan.locator('[data-org-bot="designer"]').tap();
  await page.waitForFunction(() => location.hash.startsWith('#/bot/designer'));
  assert.equal(await avatar.getAttribute('aria-expanded').catch(() => 'false'), 'false');
  assert.deepEqual(api.errors, []);
  await page.close();

  ({page} = api = await open(browser, {width: 1280}));
  await page.locator('#bot-thread .p-way:not([hidden])').waitFor();
  assert.equal(await page.locator('#bot-thread .p-bot').isHidden(), true, 'no avatar in the bar on a computer');
  await page.close();
}

// Stop: while the thread's run goes, Stop takes Send's place until something is typed, and posts the stop for that run;
// it stays pressed until the run ends, then Send is back. Someone who does not manage the bot has no Stop.
async function stop(browser) {
  let api = await open(browser, {width: 390, execution: {state: 'running', run_id: 'a1', label: 'Working'}}), {page} = api;
  const stopBtn = page.locator('#bot-thread .p-stop'), send = page.locator('#bot-thread .p-send'), box = page.locator('#bot-thread .p-text');
  await stopBtn.waitFor();
  assert.ok(api.snapshots.includes(HOME), 'the Home reads its own run');
  assert.equal(await send.isHidden(), true, 'Stop in place of Send');
  const l = await layout(page);
  const mid = await stopBtn.evaluate(b => { const r = b.getBoundingClientRect(); return Math.round(r.top + r.height / 2); });
  assert.ok(Math.abs(mid - l.text) <= 3 && l.pill < 60, 'on the one line');
  await shot(page, 'running-phone-dark');
  await box.fill('One more thing');
  assert.equal(await stopBtn.isHidden(), true, 'typing brings Send back');
  assert.equal(await send.isHidden(), false);
  await box.fill('');
  await stopBtn.waitFor();
  await stopBtn.tap();
  await page.waitForFunction(() => document.querySelector('#bot-thread .p-stop')?.disabled);
  assert.deepEqual(api.stops, [{run_id: 'a1'}]);
  assert.equal(await stopBtn.getAttribute('title'), 'Stopping…');
  // The run ends: the thread's run event reads the snapshot again, and Send is back.
  api.execution = {state: 'canceled', run_id: 'a1', label: 'Stopped'};
  await page.evaluate(tid => liveEmit('runs', {conversation_id: tid, run_id: 'a1', state: 'canceled'}), HOME);
  await stopBtn.waitFor({state: 'hidden'});
  assert.equal(await send.isHidden(), false);
  // A run starting later (a message just sent) shows Stop again; its output steps do not read anything.
  const reads = api.snapshots.length;
  await page.evaluate(tid => liveEmit('runs', {conversation_id: tid, run_id: 'a2', output: {kind: 'delta'}}), HOME);
  await page.waitForTimeout(250);
  assert.equal(api.snapshots.length, reads, 'output steps read nothing');
  api.execution = {state: 'running', run_id: 'a2', label: 'Working'};
  await page.evaluate(tid => liveEmit('runs', {conversation_id: tid, run_id: 'a2', state: 'running'}), HOME);
  await stopBtn.waitFor();
  assert.deepEqual(api.errors, []);
  await page.close();

  ({page} = api = await open(browser, {width: 1280, role: 'member', execution: {state: 'running', run_id: 'a1'}}));
  await page.waitForFunction(() => BOT?.thread?.view?.execution);
  assert.equal(await page.locator('#bot-thread .p-stop').isHidden(), true, 'no Stop for someone who does not manage the bot');
  assert.equal(await page.locator('#bot-thread .p-send').isHidden(), false);
  await page.close();
}

// "/" opens the old chat's command menu; a command does what it did there.
async function slash(browser) {
  let api = await open(browser, {width: 390, commands: COMMANDS}), {page} = api;
  const box = page.locator('#bot-thread .p-text'), menu = page.locator('#bot-thread .slash-menu'), out = page.locator('#bot-thread .p-cmd');
  await page.locator('#bot-thread .p-way:not([hidden])').waitFor();
  await box.fill('/');
  await menu.waitFor();
  assert.deepEqual(await menu.locator('.slash-name').allInnerTexts(), ['/goal', '/task', '/help', '/compact'], "the bot's commands; a Home has no /new");
  await shot(page, 'slash-phone-dark');
  // It narrows as you type; Return picks the row, and Return again runs it.
  await box.fill('/he');
  assert.deepEqual(await menu.locator('.slash-name').allInnerTexts(), ['/help']);
  await box.press('Enter');
  assert.equal(await box.inputValue(), '/help');
  await menu.waitFor({state: 'hidden'});
  await page.locator('#bot-thread .p-send').click();
  await out.locator('.slash-help-out').waitFor();
  assert.match(await out.innerText(), /\/compact/);
  assert.equal(await box.inputValue(), '');
  // A harness's command goes to the bot, marked as a command, in this thread; never as a thread message.
  await box.fill('/compact');
  await page.locator('#bot-thread .p-send').click();
  await page.waitForFunction(() => !document.querySelector('#bot-thread .p-text').value);
  assert.deepEqual(api.commands, [{to: `bot:${BOT}`, text: '/compact', conversation_id: HOME, command: true}]);
  // /task makes a task for this bot; /goal sets the thread's goal; /clear asks first; anything else is a message.
  await box.fill('/task Write the pricing brief');
  await page.locator('#bot-thread .p-send').click();
  await page.locator('.toast').filter({hasText: 'Task for'}).waitFor();
  assert.deepEqual(api.tasks, [{title: 'Write the pricing brief', body: 'Write the pricing brief', owner: BOT}]);
  await box.fill('/goal Ship the Acme pricing page');
  await page.locator('#bot-thread .p-send').click();
  await page.locator('#bot-thread .thv-goal .cg-text').waitFor();
  assert.deepEqual(api.goalPosts, [{tid: HOME, action: 'set', objective: 'Ship the Acme pricing page'}]);
  await box.fill('/clear');
  await page.locator('#bot-thread .p-send').click();
  await out.getByRole('button', {name: 'Clear session'}).waitFor();
  await box.fill('/unknown words');
  await page.locator('#bot-thread .p-send').click();
  await page.waitForFunction(() => !document.querySelector('#bot-thread .th-pending'));
  assert.deepEqual(api.sent.map(b => b.text), ['/unknown words'], 'a word the bot does not list goes as a message');
  assert.deepEqual(api.errors, []);
  await page.close();

  // A DM starts over with /new: it is archived, and the next message makes a fresh one.
  ({page} = api = await open(browser, {width: 1280, tab: 'dm', commands: COMMANDS}));
  await page.locator('#bot-thread .p-way:not([hidden])').waitFor();
  await page.locator('#bot-thread .p-text').fill('/n');
  assert.deepEqual(await page.locator('#bot-thread .slash-menu .slash-name').allInnerTexts(), ['/new']);
  await page.locator('#bot-thread .p-text').fill('/new');
  await page.locator('#bot-thread .p-send').click();
  await page.waitForFunction(() => BOT?.thread?.view?.state === 'empty' && !BOT.thread.view.id);
  assert.deepEqual(api.patches, [{p: `/api/v2/threads/${DM}`, archived: true}]);
  assert.deepEqual(api.errors, []);
  await page.close();
}

(async () => {
  const browser = await launch();
  try {
    await looks(browser);
    await goalMode(browser);
    await ways(browser);
    await emptyDm(browser);
    await refusals(browser);
    await lines(browser);
    await botSwitch(browser);
    await stop(browser);
    await slash(browser);
    console.log('thread composer: one line, one frame, ways to send, goal mode on Home and DM, refusals, goal lines, bot switcher, Stop, "/" commands: ok');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
