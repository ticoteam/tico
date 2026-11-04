// A daily update request and the bot's turn that writes the update, in a thread's timeline (ui/app/thread-view.js): one
// muted "› Update generated" line stands for both rows. A click shows them under the line and a second click shuts them
// again; the keyboard does the same and the focus stays on the line, and a redraw keeps the choice. An ordinary bot reply,
// one that does not follow a request, renders as it always did. At 390px the line has a finger's height and nothing
// scrolls sideways. Fixtures only.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');

const now = Date.now(), at = min => new Date(now - min * 60000).toISOString();
const me = {member: true, following: true, notify: 'live', last_read_seq: 6, unread: 0, activity: false, can_post: true, can_manage: false};
const thread = {id: 't-upd', title: 'Daily updates', audience: 'members', last_seq: 6, last_unread_seq: 0, last_message_at: at(5), me, members: []};
const msg = (id, seq, from, body, min, extra = {}) => ({id, seq, thread_id: 't-upd', sender_id: from, body, created_at: at(min), reply_to: null, routed: null, ...extra});
// The server's request: a keeper notice to the bot, carrying the queue id and the wake that starts the update's run.
const request = (id, seq, bot, min) => msg(id, seq, 'keeper', "Write today's update: what you did, what is next and what is blocked.", min,
  {recipient_id: bot, kind: 'notice', refs: {update_request: 'q-' + id, wake: 'update', trigger: 'schedule', trigger_source: 'q-' + id, purpose: 'write update'}});
const messages = () => [
  msg('a1', 1, 'human:ana', 'Can you check the Acme invoices before the run?', 30),
  msg('a2', 2, 'bot:ops', 'Yes, I will check them now.', 29, {recipient_id: 'human:ana'}),
  request('u1', 3, 'bot:ops', 20),
  msg('u2', 4, 'bot:ops', 'Update: the invoice run is checked and queued for 4pm.', 19,
    {recipient_id: 'keeper', in_reply_to: 'u1', refs: {turn_id: 'turn-u1'}, run: {steps: 2, tool_calls: 1, took_s: 3}}),
  msg('a3', 5, 'human:ana', 'Thanks, that helps.', 10, {recipient_id: 'bot:ops'}),
  msg('a4', 6, 'bot:ops', 'Anything else you want checked?', 5, {recipient_id: 'human:ana'}),
];

// The thread page with its API faked: the timeline above, and empty answers for what the page reads beside it.
async function open(browser, {phone = false} = {}) {
  const page = await browser.newPage({viewport: phone ? {width: 390, height: 844} : {width: 1280, height: 800}, serviceWorkers: 'block', reducedMotion: 'reduce',
    ...(phone ? {isMobile: true, hasTouch: true} : {})});
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname, method = req.method();
    const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1])))
      return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
    if (/\/marked\.min\.js$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: fs.readFileSync(uiFile('vendor/marked.min.js'), 'utf8')});
    if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
    if (p === '/api/me') return json({id: 'ana', name: 'Ana', role: 'owner', registered: true});
    if (p === '/api/employees') return json([{name: 'ops', display_name: 'Ops', host: 'keeper', status: 'active', state: 'active', can_chat: true, schedules: [],
      my_access: {see: true, read: true, write: true}}]);
    if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana', email: 'ana@acme.example'}], org_groups: []});
    // The live stream is not needed here: the timeline is read once.
    if (p === '/api/v2/events') return route.abort();
    if (p === '/api/v2/threads/rail') return json({pinned: [], threads: [thread], catch_up: 0, next_cursor: null, dms: {}});
    if (p === '/api/v2/threads' && method === 'GET') return json({attention: [], threads: [thread], dms: [], next_cursor: null});
    if (p === '/api/v2/rule-holds') return json({holds: [], next_cursor: null});
    if (p === '/api/v2/threads/t-upd/timeline') return json({thread, breadcrumb: [], next_cursor: null, messages: messages(), bot_status: [], read_marks: {'t-upd': 6}});
    if (/^\/api\/v2\/threads\/[^/]+\/(read|members|calls)$/.test(p)) return json(p.endsWith('calls') ? {calls: [], next_before: null, available: false}
      : p.endsWith('members') ? {members: [], next_cursor: null} : {ok: true});
    if (/^\/api\/v2\/threads\/[^/]+\/bot-status$/.test(p)) return json({bots: []});
    return json(unrouted(p));
  });
  return {page, errors};
}

const row = (page, id) => page.locator(`.th-msg[data-mid="${id}"]`);
const line = page => page.locator('.th-upd-btn');
// The messages inside the panel a line controls, in order.
const panelRows = (page, panel) => page.evaluate(id => [...document.getElementById(id).querySelectorAll('.th-msg')].map(e => e.dataset.mid), panel);

let browser = null;
(async () => {
  browser = await chromium.launch();

  // ---- a request and the bot's reply, desktop
  {
    const {page, errors} = await open(browser);
    await page.goto('https://tico-ui.test/#/thread/t-upd');
    await row(page, 'a4').waitFor();

    // An ordinary bot reply, before any request and after a person's message, renders as it always did.
    for (const id of ['a2', 'a4']) assert.equal(await row(page, id).locator('.th-head .th-bot').count(), 1, id + ' keeps its header and bot tag');
    assert.match(await row(page, 'a2').locator('.th-body').textContent(), /Yes, I will check them now/);
    assert.match(await row(page, 'a4').locator('.th-body').textContent(), /Anything else/);
    assert.equal(await row(page, 'a1').count(), 1);
    assert.equal(await row(page, 'a3').count(), 1);

    // The request and the bot's reply are one line, and neither row shows.
    await line(page).waitFor();
    assert.equal(await page.locator('.th-upd-btn').count(), 1, 'one line for the pair');
    assert.equal(await line(page).textContent(), '› Update generated');
    assert.equal(await line(page).getAttribute('aria-expanded'), 'false');
    assert.equal(await row(page, 'u1').count(), 0, 'the request is hidden');
    assert.equal(await row(page, 'u2').count(), 0, "the bot's reply is hidden");
    const panel = await line(page).getAttribute('aria-controls');
    assert.equal(await page.evaluate(id => document.getElementById(id)?.childElementCount, panel), 0, 'nothing under a shut line');

    // A click shows both rows under the line, and a second click shuts them again.
    await line(page).click();
    await row(page, 'u2').waitFor();
    assert.equal(await line(page).getAttribute('aria-expanded'), 'true');
    assert.deepEqual(await panelRows(page, panel), ['u1', 'u2'], 'the request, then the reply, under the line');
    await line(page).click();
    await row(page, 'u2').waitFor({state: 'detached'});
    assert.equal(await row(page, 'u1').count(), 0);
    assert.equal(await line(page).getAttribute('aria-expanded'), 'false');

    // The keyboard does the same, and the focus stays on the line.
    await line(page).focus();
    await page.keyboard.press('Enter');
    await row(page, 'u1').waitFor();
    assert.equal(await page.evaluate(() => document.activeElement?.dataset.thUpdate), 'u1', 'focus stays on the line');
    await page.keyboard.press('Enter');
    await row(page, 'u1').waitFor({state: 'detached'});
    assert.equal(await page.evaluate(() => document.activeElement?.dataset.thUpdate), 'u1', 'focus stays on the line when shut');

    // A redraw keeps the choice.
    await line(page).click();
    await row(page, 'u2').waitFor();
    await page.evaluate(() => thvDraw([...THV_VIEWS].find(v => v.id === 't-upd')));
    assert.equal(await row(page, 'u2').count(), 1, 'a redraw keeps the line open');
    assert.deepEqual(await page.evaluate(() => [...document.querySelectorAll('[data-th-update]')].map(b => b.getAttribute('aria-expanded'))), ['true']);

    assert.deepEqual(errors, []);
    await page.close();
  }

  // ---- a phone at 390px: the line has a finger's height, and nothing scrolls sideways with the rows open
  {
    const {page, errors} = await open(browser, {phone: true});
    await page.goto('https://tico-ui.test/#/thread/t-upd');
    await line(page).waitFor();
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth) <= 390, 'no sideways scroll');
    assert.ok((await line(page).boundingBox()).height >= 44, "the line is a finger's height");
    await line(page).click();
    await row(page, 'u2').waitFor();
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth) <= 390, 'no sideways scroll with the rows open');
    assert.deepEqual(errors, []);
    await page.close();
  }
  console.log('update generated line: ok');
})().then(() => browser?.close(), async e => { console.error(e); await browser?.close(); process.exit(1); });
