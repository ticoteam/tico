// A chat's goal on the Assistant page, offline (the one old chat left; a bot's page is Home and DM now). The goal
// target, the "/" menu, the ways to send and the pinned-goal bar live in a bot's own composer, which is gone, so only
// what the thread itself draws is here: the server's notice for a met goal (refs.chat_goal, refs.goal_status) is the
// compact goal line, not a "Task" line, and the /goal messages the goal controls sent are not bubbles.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const {assistantView} = require('./support/assistant-chat.cjs');
const launch = () => chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});

async function open(browser, viewport, touch, {messages, goal, theme = ''}) {
  const page = await browser.newPage({viewport, serviceWorkers: 'block', ...(touch ? {hasTouch: true, isMobile: true} : {})});
  if (theme) await page.addInitScript(t => { try { localStorage.setItem('tico.theme', t); } catch {} }, theme);
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.route('**/*', async route => {
    const url = new URL(route.request().url()), p = url.pathname;
    const json = body => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1])))
      return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
    if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    if (p === '/api/me') return json({id: 'ana', name: 'Ana', role: 'owner', cloud: true});
    if (p === '/api/employees') return json([{name: 'coo', display_name: 'Assistant', host: 'keeper', status: 'active', can_chat: true, schedules: []}]);
    if (p.startsWith('/vendor/fonts/') && fs.existsSync(uiFile(p.slice(1)))) return route.fulfill({contentType: 'font/woff2', body: fs.readFileSync(uiFile(p.slice(1)))});
    if (p === '/api/issues') return json([]);
    if (p === '/api/v2/assistant') return json(assistantView('c1', {messages}));
    if (p === '/api/v2/conversations/c1/snapshot') return json({messages, execution: null});
    if (p === '/api/v2/events' || p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    if (p === '/api/v2/conversations/c1/goal') return json({goal, supported: true, commands: []});
    return json(unrouted(p));
  });
  await page.goto('https://tico-ui.test/#/assistant');
  await page.locator('#chat-composer textarea').waitFor();
  return {errors, page};
}

async function serverNotice(browser) {
  const t = (m, s) => `2026-10-01T09:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}Z`;
  const messages = [
    {id: 'm1', kind: 'say', from_actor: 'human:ana', body: '/goal Produce the revised Acme summary', created: t(1, 0),
     refs: {command: true, goal_action: 'set', goal_id: 'g9', goal_revision: t(1, 0), goal_objective: 'Produce the revised Acme summary'}},
    {id: 'm2', kind: 'say', from_actor: 'human:ana', body: '/goal clear', created: t(2, 0), refs: {command: true, goal_action: 'pause', goal_id: 'g9'}},
    {id: 'm3', kind: 'say', from_actor: 'human:ana', body: '/goal Produce the revised Acme summary', created: t(3, 0), refs: {command: true, goal_action: 'resume', goal_id: 'g9'}},
    {id: 'm4', kind: 'say', from_actor: 'bot:coo', body: 'Summary revised and posted.', created: t(9, 0), refs: {}},
    // As backend/chat_goals.py writes it: refs {chat_goal, goal_status}; a two-line objective, then the note.
    {id: 'm5', kind: 'notice', from_actor: 'bot:coo', body: 'Goal met: Produce the revised Acme summary\nwith the Q3 numbers\nThe condition holds.', created: t(9, 5),
     refs: {chat_goal: 'g9', goal_status: 'met'}},
  ];
  const goal = {id: 'g9', conversation_id: 'c1', bot: 'coo', objective: 'Produce the revised Acme summary\nwith the Q3 numbers', status: 'met',
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


(async () => {
  const browser = await launch();
  try { await serverNotice(browser); }
  finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
