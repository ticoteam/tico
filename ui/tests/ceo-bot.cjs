// The CEO Bot switch (backend/ceo_bot.py, `ceo_bot` in the config GET /api/me and /v2/config send): on, the Goal Manager is on the org chart
// by its display name, the CEO Bot, with the bots and the Heads' departments the server hangs under it, and the Goals page calls it by
// that name; off, it stays apart and is the Goal Manager, as ui/tests/goals.cjs checks too. A switch flipped while Goals is open
// shows without a reload and keeps a draft. Fixtures only, no network.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');

const bot = (name, display_name, org_parent, over) => ({name, display_name, org_parent, host: 'keeper', status: 'active', can_chat: true, ...over});
// What the server sends either way: with the switch on it has renamed the bot and hung the Heads, and the Head's
// department (Marketing), under it.
const bots = (on, home) => [
  bot('coo', 'Assistant', 'p:ana'), bot('botops', 'BotOps', 'b:coo'), bot('librarian', 'Librarian', 'p:ana'),
  bot('goal-manager', on ? 'CEO Bot' : 'Goal Manager', 'p:ana', {reports_to: ''}),
  bot('cmo', 'Head of Marketing', on ? 'b:goal-manager' : 'p:ana', {reports_to: '', team: 'marketing'}),
  bot('seo', 'SEO', 'b:cmo', {reports_to: 'cmo', team: 'marketing'}),
  bot('scribe', 'Scribe', on ? 'b:goal-manager' : 'p:ana', {reports_to: '', ...(home ? {team: 'home'} : {})}),
];
// With `home`, the owner and Scribe are in a group of their own, which the server never hangs under the CEO Bot.
const groups = (on, home) => [{id: 'marketing', name: 'Marketing', reports_to: '', parent: '', org_parent: on ? 'b:goal-manager' : '', order: 0},
  ...(home ? [{id: 'home', name: 'Home', reports_to: '', parent: '', org_parent: '', order: 1}] : [])];

async function open(browser, server, width = 1440) {
  const page = await browser.newPage({viewport: {width, height: 900}, serviceWorkers: 'block'});
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname, on = server.on, home = !!server.home;
    const json = body => route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify(body)});
    if (url.origin !== 'http://tico-ui.test') return route.abort();
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
    if (ui) { const file = uiFile(ui[1]); if (fs.existsSync(file)) return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(file, 'utf8')}); }
    if (p === '/api/v2/events') return;
    if (p === '/api/v2/config') return json({ceo_bot: on});
    if (p === '/api/employees') return json(bots(on, home));
    if (p === '/api/me') return json({id: 'ana', name: 'Ana Silva', email: 'ana@example.test', role: 'owner', mover: true, cloud: true, config: {ceo_bot: on}});
    if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana Silva', org_parent: '', ...(home ? {team: 'home'} : {})}], org_groups: groups(on, home)});
    if (p === '/api/status') return json({active: [], employees: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/goals/tree') return json({goals: [], owners: {}, other_kpis: [], proposals: []});
    if (p === '/api/v2/goals/needs-you') return json({actor: 'human:ana', items: []});
    if (p === '/api/v2/bots/goal-manager/routines') return json({routines: []});
    return json(unrouted(p));
  });
  await page.goto('http://tico-ui.test/#/goals');
  await page.locator('#goal-tree .gt-row').first().waitFor();
  await page.locator('#goal-manager-panel h2').waitFor();
  return {page, errors};
}
const goalLines = page => page.locator('#goal-tree > li').evaluateAll(els => els
  .map(el => `${el.dataset.owner || (el.classList.contains('gt-group') ? 'group:' + el.textContent.trim() : '')}@${el.style.getPropertyValue('--d')}`)
  .filter(l => l.startsWith('bot:') || l.startsWith('group:')));
// Every chart node above this one, nearest first.
const above = (page, key) => page.locator(`#tree [data-org="${key}"]`).first().evaluate(el => {
  const out = [];
  for (let li = el.closest('li')?.parentElement?.closest('li'); li; li = li.parentElement?.closest('li'))
    out.push(li.querySelector(':scope > .noderow [data-org], :scope > .noderow[data-org]')?.dataset.org
      || li.querySelector(':scope > .noderow')?.dataset.org || '');
  return out;
});

async function checkOn(page) {
  // On the chart, by its name, under the owner, with the Head's department, the Head, its report and the bot with
  // no manager under it.
  const ceo = page.locator('#tree a.node[data-org="b:goal-manager"]');
  assert.equal(await ceo.count(), 1, 'the CEO Bot is on the chart');
  assert.equal(await ceo.getAttribute('data-helper'), null, 'not set apart with the built-ins');
  assert.equal((await ceo.locator('.nm').innerText()).trim(), 'CEO Bot');
  assert.deepEqual(await above(page, 'b:goal-manager'), ['p:ana']);
  assert.deepEqual(await above(page, 'g:marketing'), ['b:goal-manager', 'p:ana']);
  assert.deepEqual(await above(page, 'b:seo'), ['b:cmo', 'g:marketing', 'b:goal-manager', 'p:ana']);
  assert.deepEqual(await above(page, 'b:scribe'), ['b:goal-manager', 'p:ana']);
  assert.equal(await page.locator('#tree a.node[data-org="b:coo"]').count(), 0, 'the Assistant stays off the chart');
  // The Goals page tree nests it the same way, and its panel and labels say CEO Bot.
  assert.deepEqual(await goalLines(page), ['bot:goal-manager@1', 'bot:scribe@2', 'group:Marketing@2', 'bot:cmo@3', 'bot:seo@4']);
  assert.equal(await page.locator('#goal-manager-panel h2').innerText(), 'CEO Bot');
  assert.equal(await page.locator('#goal-manager-panel').getAttribute('aria-label'), 'CEO Bot');
  assert.equal(await page.locator('#goal-manager-panel .p-text').getAttribute('aria-label'), 'Message to the CEO Bot');
  assert.doesNotMatch(await page.locator('#main').innerText(), /Goal Manager/);
}
async function checkOff(page) {
  // Off: the Goal Manager keeps its name and stays apart; the department and the bots hang where they did.
  assert.equal(await page.locator('#tree a.node[data-org="b:goal-manager"]').count(), 0);
  assert.deepEqual(await above(page, 'g:marketing'), []);
  assert.deepEqual(await above(page, 'b:seo'), ['b:cmo', 'g:marketing']);
  assert.equal(await page.locator('#goal-manager-panel h2').innerText(), 'Goal Manager');
  assert.equal(await page.locator('#goal-manager-panel').getAttribute('aria-label'), 'Goal Manager');
  assert.equal(await page.locator('#goal-manager-panel .p-text').getAttribute('aria-label'), 'Message to the Goal Manager');
  assert.deepEqual(await goalLines(page), ['bot:scribe@1', 'group:Marketing@0', 'bot:cmo@1', 'bot:seo@2']);
  assert.doesNotMatch(await page.locator('#main').innerText(), /CEO Bot/);
}
// Another admin flips the switch: the page learns it from its config poll, as the 30-second one does.
async function flip(page, server, on) {
  server.on = on;
  await page.evaluate(() => refreshConfig());
  await page.waitForFunction(want => document.querySelector('#goal-manager-panel h2')?.textContent === want
    && !!document.querySelector('#tree a.node[data-org="b:goal-manager"]') === (want === 'CEO Bot'), on ? 'CEO Bot' : 'Goal Manager');
}

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    for (const on of [true, false]) {
      const {page, errors} = await open(browser, {on});
      await (on ? checkOn : checkOff)(page);
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // Off, then on, then off again in one open page: the names and both trees follow, and a draft is kept.
      const server = {on: false};
      const {page, errors} = await open(browser, server);
      const draft = page.locator('#goal-manager-panel .p-text');
      await draft.fill('Move the launch goal to May');
      await flip(page, server, true);
      await checkOn(page);
      await flip(page, server, false);
      await checkOff(page);
      assert.equal(await draft.inputValue(), 'Move the launch goal to May');
      assert.deepEqual(errors, []);
      await page.close();
    }
    // The owner in a group of their own, with a bot that has no manager: the CEO Bot still hangs under the owner, and
    // that bot leaves the owner's group for the CEO Bot's branch, at desktop and phone widths.
    for (const width of [1280, 390]) {
      const server = {on: true, home: true};
      const {page, errors} = await open(browser, server, width);
      assert.deepEqual(await above(page, 'b:goal-manager'), ['p:ana', 'g:home'], `${width}: the CEO Bot under the owner`);
      assert.deepEqual(await above(page, 'b:scribe'), ['b:goal-manager', 'p:ana', 'g:home'], `${width}: Scribe under the CEO Bot`);
      assert.deepEqual(await above(page, 'b:seo'), ['b:cmo', 'g:marketing', 'b:goal-manager', 'p:ana', 'g:home']);
      assert.deepEqual(await goalLines(page), ['group:Home@0', 'bot:goal-manager@2', 'bot:scribe@3', 'group:Marketing@3', 'bot:cmo@4', 'bot:seo@5']);
      await flip(page, server, false);
      assert.deepEqual(await above(page, 'b:scribe'), ['p:ana', 'g:home'], `${width}: off, Scribe back in the owner's group`);
      assert.deepEqual(await above(page, 'g:marketing'), []);
      assert.deepEqual(await goalLines(page), ['group:Marketing@0', 'bot:cmo@1', 'bot:seo@2', 'group:Home@0', 'bot:scribe@2']);
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log('ceo-bot: ok');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
