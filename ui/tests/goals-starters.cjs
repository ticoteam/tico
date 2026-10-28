// The CEO Bot's chat in the Goals rail (ui/app/goals.js goalManagerMount), chat first: the owner reads that it drafts a
// goal and saves it on their yes, the box asks them to set or fix a goal, and an empty chat offers three starters. A
// tap sends the starter's words as the owner's message, and once the chat has a message the starters are gone. Anyone
// else still reads that only the owner talks to the CEO Bot and is pointed to their Assistant. Fixtures only.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');

const bot = (name, display_name, over) => ({name, display_name, org_parent: '', host: 'keeper', status: 'active', can_chat: true, ...over});
const bots = [bot('coo', 'Assistant'), bot('botops', 'BotOps'), bot('goal-manager', 'CEO Bot', {org_parent: 'p:ana', reports_to: ''})];
const STARTERS = ['Set a goal', 'Fix my goals', 'Go through suggestions'];

async function open(browser, {role = 'owner', chat = []} = {}) {
  const page = await browser.newPage({viewport: {width: 1440, height: 900}, serviceWorkers: 'block'});
  const errors = [], posted = [], messages = [...chat];
  page.on('pageerror', e => errors.push(e.message));
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname, post = req.method() === 'POST';
    const json = body => route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify(body)});
    if (url.origin !== 'http://tico-ui.test') return route.abort();
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
    if (ui) { const file = uiFile(ui[1]); if (fs.existsSync(file)) return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(file, 'utf8')}); }
    if (p === '/api/v2/events') return;
    if (p === '/api/v2/config') return json({});
    if (p === '/api/employees') return json(bots);
    if (p === '/api/me') return json({id: role === 'owner' ? 'ana' : 'cara', name: role === 'owner' ? 'Ana Silva' : 'Cara Diaz', email: 'x@example.test',
      role: role === 'owner' ? 'owner' : 'admin', company_role: role, mover: true, cloud: true, config: {}});
    if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana Silva', org_parent: ''}, {id: 'cara', name: 'Cara Diaz', org_parent: 'p:ana'}]});
    if (p === '/api/status') return json({active: [], employees: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/goals/tree') return json({goals: [], owners: {}, other_kpis: [], proposals: []});
    if (p === '/api/v2/goals/needs-you') return json({actor: 'human:ana', items: []});
    if (p === '/api/v2/bots/goal-manager/routines') return json({routines: []});
    if (p === '/api/v2/conversations') return json({conversations: messages.length ? [{id: 'gm-chat', kind: 'chat', room_key: 'ceo'}] : []});
    if (p === '/api/v2/chat/goal-manager' && post) {
      const body = req.postDataJSON();
      posted.push({path: p, body});
      messages.push({id: 'm' + messages.length, from_actor: 'human:ana', body: body.text});
      return json({conversation: {id: 'gm-chat'}, message: messages.at(-1)});
    }
    if (p === '/api/v2/conversations/gm-chat/snapshot') return json({messages});
    return json(unrouted(p));
  });
  await page.goto('http://tico-ui.test/#/goals');
  await page.locator('#goal-manager-panel .gm-what').waitFor();
  return {page, errors, posted};
}

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    // Test 1: the owner with an empty chat reads the chat-first line, the new placeholder and the three starters.
    {
      const {page, errors, posted} = await open(browser);
      const rail = page.locator('#goal-manager-panel');
      await rail.locator('[data-gm-starter]').first().waitFor();
      assert.equal(await rail.locator('.gm-what').innerText(),
        'Keeps KPIs current and each goal green, yellow or red. Tell it what you want to achieve. It drafts the goal and saves it when you say yes.');
      assert.doesNotMatch(await rail.innerText(), /Humans set the goals/);
      assert.equal(await rail.locator('.gm-form textarea').getAttribute('placeholder'), 'Set or fix a goal…');
      assert.deepEqual(await rail.locator('[data-gm-starter]').allInnerTexts(), STARTERS);
      // Test 2: a tap sends its words, as typed by the owner, to the CEO Bot's chat.
      await rail.locator('[data-gm-starter]', {hasText: 'Go through suggestions'}).click();
      await rail.locator('[data-gm-thread] .bubble.you', {hasText: 'Go through suggestions'}).waitFor();
      assert.deepEqual(posted, [{path: '/api/v2/chat/goal-manager', body: {text: 'Go through suggestions', refs: {}}}]);
      // Test 3: with a message in the chat the starters are gone.
      assert.equal(await rail.locator('[data-gm-starter]').count(), 0, 'no starters once the chat has a message');
      assert.equal(await rail.locator('.gm-form textarea').inputValue(), '', 'the box stays empty');
      assert.deepEqual(errors, []);
      await page.close();
    }
    // A chat that already has messages opens with no starters.
    {
      const {page, errors} = await open(browser, {chat: [{id: 'm0', from_actor: 'human:ana', body: 'Hello'}, {id: 'm1', from_actor: 'bot:goal-manager', body: 'Hi.'}]});
      await page.locator('[data-gm-thread] .bubble.bot', {hasText: 'Hi.'}).waitFor();
      assert.equal(await page.locator('[data-gm-starter]').count(), 0);
      assert.deepEqual(errors, []);
      await page.close();
    }
    // Test 4: anyone else (an admin here) is still sent to their Assistant: no starters, no chat-first line.
    {
      const {page, errors, posted} = await open(browser, {role: 'admin'});
      const rail = page.locator('#goal-manager-panel');
      await rail.locator('[data-gm-assistant]').waitFor();
      assert.equal((await rail.locator('[data-gm-assistant]').textContent()).replace(/\s+/g, ' ').trim(), "Only the company's owner talks to CEO Bot. Ask your Assistant instead.");
      assert.equal(await rail.locator('[data-gm-assistant] a').getAttribute('href'), '#/assistant');
      assert.match(await rail.locator('.gm-what').innerText(), /Humans set the goals\.$/);
      assert.equal(await rail.locator('[data-gm-starter]').count(), 0);
      assert.equal(await rail.locator('.gm-form').count(), 0);
      assert.deepEqual(posted, []);
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log('goals-starters: ok');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
