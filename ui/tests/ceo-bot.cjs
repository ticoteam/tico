// The CEO Bot on every install (backend/ceo_bot.py): the built-in bot is on the org chart by its name, the CEO Bot unless the owner
// renamed it, with the bots and the Heads' departments the server hangs under it, and the Goals page calls it by that name. A bot
// still named "Goal Manager" (an install the upgrade has not reached) shows as the CEO Bot; the old name is never shown. There is no
// switch to flip. A person may hide it from their own chart (Hide from my chart) and bring it back from Settings > Bots.
// Fixtures only, no network.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');

const bot = (name, display_name, org_parent, over) => ({name, display_name, org_parent, host: 'keeper', status: 'active', can_chat: true, ...over});
// What the server sends: the Heads, and the Head's department (Marketing), hang under the CEO Bot. For a person who chose
// Hide from my chart (`hidden`), nothing is moved under it and its row says `chart_hidden` (backend/ceo_bot.py).
const bots = (name, home, hidden, analyst) => [
  bot('coo', 'Assistant', 'p:ana'), bot('botops', 'BotOps', 'b:coo'), bot('librarian', 'Librarian', 'p:ana'),
  bot('goal-manager', name, 'p:ana', {reports_to: '', can_manage: true, ...(hidden ? {chart_hidden: true} : {})}),
  bot('cmo', 'Head of Marketing', hidden ? 'g:marketing' : 'b:goal-manager', {reports_to: '', team: 'marketing'}),
  bot('seo', 'SEO', 'b:cmo', {reports_to: 'cmo', team: 'marketing'}),
  bot('scribe', 'Scribe', hidden ? '' : 'b:goal-manager', {reports_to: '', operator: 'ana', ...(home ? {team: 'home'} : {})}),
  // Reports to the CEO Bot itself and runs on Bo's computer: hidden, it hangs from the next row up, the owner.
  ...(analyst ? [bot('analyst', 'Analyst', 'b:goal-manager', {reports_to: 'goal-manager', operator: 'bo'})] : []),
];
// With `home`, the owner and Scribe are in a group of their own, which the server never hangs under the CEO Bot.
const groups = (home, hidden) => [{id: 'marketing', name: 'Marketing', reports_to: '', parent: '', org_parent: hidden ? '' : 'b:goal-manager', order: 0},
  ...(home ? [{id: 'home', name: 'Home', reports_to: '', parent: '', org_parent: '', order: 1}] : [])];

async function open(browser, server, width = 1440) {
  const page = await browser.newPage({viewport: {width, height: 900}, serviceWorkers: 'block'});
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname, home = !!server.home;
    const json = body => route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify(body)});
    if (url.origin !== 'http://tico-ui.test') return route.abort();
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
    if (ui) { const file = uiFile(ui[1]); if (fs.existsSync(file)) return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(file, 'utf8')}); }
    if (p === '/api/v2/events') return;
    if (p === '/api/v2/config') return json({});
    if (p === '/api/employees') return json(bots(server.name ?? 'CEO Bot', home, server.hidden, server.analyst));
    if (p === '/api/me') return json({id: 'ana', name: 'Ana Silva', email: 'ana@example.test', role: 'owner', mover: true, cloud: true, config: {}});
    if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana Silva', org_parent: '', ...(home ? {team: 'home'} : {})}], org_groups: groups(home, server.hidden)});
    if (p === '/api/v2/preferences/chart.hide_ceo_bot' && req.method() === 'POST') {
      const body = req.postDataJSON(); (server.saves ||= []).push(body.value); server.hidden = body.value;
      return json({key: 'chart.hide_ceo_bot', value: body.value});
    }
    if (p === '/api/v2/models') return json({models: [], harnesses: [], enabled_providers: []});
    if (p === '/api/v2/operations') return json({machines: [], services: [], agents: []});
    if (p === '/api/v2/usage/limits') return json({bots: {}});
    if (p === '/api/v2/settings/history') return json({changes: [], transitions: []});
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

async function check(page, name = 'CEO Bot') {
  // On the chart, by its name, under the owner, with the Head's department, the Head, its report and the bot with
  // no manager under it.
  const ceo = page.locator('#tree a.node[data-org="b:goal-manager"]');
  assert.equal(await ceo.count(), 1, 'the CEO Bot is on the chart');
  assert.equal(await ceo.getAttribute('data-helper'), null, 'not set apart with the built-ins');
  assert.equal((await ceo.locator('.nm').innerText()).trim(), name);
  assert.deepEqual(await above(page, 'b:goal-manager'), ['p:ana']);
  assert.deepEqual(await above(page, 'g:marketing'), ['b:goal-manager', 'p:ana']);
  assert.deepEqual(await above(page, 'b:seo'), ['b:cmo', 'g:marketing', 'b:goal-manager', 'p:ana']);
  assert.deepEqual(await above(page, 'b:scribe'), ['b:goal-manager', 'p:ana']);
  assert.equal(await page.locator('#tree a.node[data-org="b:coo"]').count(), 0, 'the Assistant stays off the chart');
  // The Goals page tree nests it the same way, and its panel and labels use its name.
  assert.deepEqual(await goalLines(page), ['bot:goal-manager@1', 'bot:scribe@2', 'group:Marketing@2', 'bot:cmo@3', 'bot:seo@4']);
  assert.equal(await page.locator('#goal-manager-panel h2').innerText(), name);
  assert.equal(await page.locator('#goal-manager-panel').getAttribute('aria-label'), name);
  assert.equal(await page.locator('#goal-manager-panel .p-text').getAttribute('aria-label'), `Message to the ${name}`);
  assert.doesNotMatch(await page.locator('#main').innerText(), /Goal Manager/);
}
(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    // Its name, the name of a bot the upgrade has not reached yet (never shown), and a name the owner gave it.
    for (const [sent, shown] of [['CEO Bot', 'CEO Bot'], ['Goal Manager', 'CEO Bot'], ['Atlas', 'Atlas']]) {
      const {page, errors} = await open(browser, {name: sent});
      await check(page, shown);
      assert.deepEqual(errors, []);
      await page.close();
    }
    // The owner in a group of their own, with a bot that has no manager: the CEO Bot still hangs under the owner, and
    // that bot leaves the owner's group for the CEO Bot's branch, at desktop and phone widths.
    for (const width of [1280, 390]) {
      const server = {home: true};
      const {page, errors} = await open(browser, server, width);
      assert.deepEqual(await above(page, 'b:goal-manager'), ['p:ana', 'g:home'], `${width}: the CEO Bot under the owner`);
      assert.deepEqual(await above(page, 'b:scribe'), ['b:goal-manager', 'p:ana', 'g:home'], `${width}: Scribe under the CEO Bot`);
      assert.deepEqual(await above(page, 'b:seo'), ['b:cmo', 'g:marketing', 'b:goal-manager', 'p:ana', 'g:home']);
      assert.deepEqual(await goalLines(page), ['group:Home@0', 'bot:goal-manager@2', 'bot:scribe@3', 'group:Marketing@3', 'bot:cmo@4', 'bot:seo@5']);
      assert.deepEqual(errors, []);
      await page.close();
    }
    // Hide from my chart, in the CEO Bot's menu on the Team sidebar, by its current name: on this person's chart it is
    // gone and the bots it headed sit where their own lines put them; Goals still has it. Settings > Bots shows it again,
    // on its row and in its editor, by the same name.
    {
      const server = {name: 'Atlas', analyst: true};
      const {page, errors} = await open(browser, server);
      // By keyboard, as for Move: Tab from its row to its menu button, open it, and the first item is Hide from my chart.
      await page.locator('#tree a.node[data-org="b:goal-manager"]').focus();
      await page.keyboard.press('Tab');
      assert.equal(await page.evaluate(() => document.activeElement.getAttribute('aria-label')), 'More for Atlas');
      await page.keyboard.press('Enter');
      assert.equal(await page.evaluate(() => document.activeElement.textContent), 'Hide from my chart');
      await page.keyboard.press('Enter');
      await page.locator('#tree a.node[data-org="b:goal-manager"]').waitFor({state: 'detached'});
      await page.waitForFunction(() => document.activeElement?.closest('.toast')
        && document.activeElement.textContent === 'Show on my chart');
      assert.deepEqual(await above(page, 'b:analyst'), ['p:ana'], 'a bot reporting to it hangs from the next row up');
      assert.deepEqual(server.saves, [true]);
      await page.locator('.toast', {hasText: 'Atlas is hidden from your chart. Settings > Bots > Atlas shows it again.'}).waitFor();
      assert.deepEqual(await above(page, 'b:cmo'), ['g:marketing']);
      assert.deepEqual(await above(page, 'g:marketing'), []);
      assert.deepEqual(await above(page, 'b:seo'), ['b:cmo', 'g:marketing']);
      assert.equal(await page.locator('#tree a.node[data-org="b:scribe"]').count(), 1);
      assert.equal(await page.locator('#goal-manager-panel h2').innerText(), 'Atlas', 'Goals still has it');
      assert.ok((await goalLines(page)).includes('bot:goal-manager@1'), 'and its line on the Goals tree');
      // Settings > Bots: still listed, saying where it is, and Show on my chart brings it back.
      await page.evaluate(() => { location.hash = '#/settings'; });
      await page.waitForFunction(() => typeof SETTINGS_DATA !== 'undefined' && SETTINGS_DATA.models);
      await page.evaluate(() => settingsShow('bots'));
      const line = page.locator('[data-settings-bot="goal-manager"] [data-ceo-chart]');
      assert.equal((await line.innerText()).trim(), 'Hidden from your chart · Show on my chart');
      await line.getByRole('button', {name: 'Show on my chart'}).click();
      await page.locator('[data-settings-bot="goal-manager"] [data-ceo-chart-toggle="hide"]').waitFor();
      assert.deepEqual(server.saves, [true, false]);
      assert.equal((await line.innerText()).trim(), 'Hide from my chart');
      assert.deepEqual(await above(page, 'b:goal-manager'), ['p:ana']);
      assert.deepEqual(await above(page, 'b:cmo'), ['g:marketing', 'b:goal-manager', 'p:ana']);
      // Its editor in Settings > Bots has the same line, saved at once and apart from Save bot.
      await page.evaluate(() => { settingsEditBot('goal-manager', true); $('#bot-editor').showModal(); });   // as its settings tab draws it
      const editor = page.locator('#bot-editor [data-ceo-chart]');
      await editor.getByRole('button', {name: 'Hide from my chart'}).click();
      await page.locator('#bot-editor [data-ceo-chart-toggle="show"]').waitFor();
      assert.deepEqual(server.saves, [true, false, true]);
      assert.match(await editor.innerText(), /^Atlas is hidden from your chart\./);
      assert.equal(await page.locator('#tree a.node[data-org="b:goal-manager"]').count(), 0);
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log('ceo-bot: ok');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
