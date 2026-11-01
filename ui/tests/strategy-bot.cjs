// The Strategy Bot on every install (backend/strategy_bot.py): a built-in bot, like BotOps and the Librarian, so it is not a row
// on the Team chart. The Heads, their departments and the bots with no manager that the server hangs under it report to
// the owner instead: on the sidebar they sit at the top beside the owner's row (the top of the chart is not one more
// level), on Goals under the owner. Nobody chats with it (the Assistant hands it work), so Strategy has no rail for it;
// where it is named (Settings > System) it goes by its name, the Strategy Bot unless the owner renamed it; a bot still named "CEO Bot" or "Goal Manager" shows as the Strategy Bot and the old names are never shown.
// There is no Hide from my chart any more: a person who once chose it sees the same chart. Fixtures only, no network.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');

const bot = (name, display_name, org_parent, over) => ({name, display_name, org_parent, host: 'keeper', status: 'active', can_chat: true, ...over});
// What the server sends: the Heads, and the Head's department (Marketing), hang under the Strategy Bot. For a person who
// once chose Hide from my chart (`hidden`), nothing is moved under it and its row says `chart_hidden` (backend/strategy_bot.py).
const bots = (name, home, hidden, analyst) => [
  bot('coo', 'Assistant', 'p:ana'), bot('botops', 'BotOps', 'b:coo'), bot('librarian', 'Librarian', 'p:ana'),
  bot('goal-manager', name, 'p:ana', {reports_to: '', can_manage: true, ...(hidden ? {chart_hidden: true} : {})}),
  bot('cmo', 'Head of Marketing', hidden ? 'g:marketing' : 'b:goal-manager', {reports_to: '', team: 'marketing'}),
  bot('seo', 'SEO', 'b:cmo', {reports_to: 'cmo', team: 'marketing'}),
  bot('scribe', 'Scribe', hidden ? '' : 'b:goal-manager', {reports_to: '', owner_id: 'ana', ...(home ? {team: 'home'} : {})}),
  // Reports to the Strategy Bot itself and runs on Bo's computer: it hangs from the next row up, the owner.
  ...(analyst ? [bot('analyst', 'Analyst', 'b:goal-manager', {reports_to: 'goal-manager', owner_id: 'bo'})] : []),
];
// With `home`, the owner and Scribe are in a group of their own, which the server never hangs under the Strategy Bot.
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
    if (p === '/api/employees') return json(bots(server.name ?? 'Strategy Bot', home, server.hidden, server.analyst));
    if (p === '/api/me') return json({id: 'ana', name: 'Ana Silva', email: 'ana@example.test', role: 'owner', mover: true, cloud: true, config: {}});
    if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana Silva', org_parent: '', ...(home ? {team: 'home'} : {})}], org_groups: groups(home, server.hidden)});
    if (p.startsWith('/api/v2/preferences/') && req.method() === 'POST') (server.saves ||= []).push(p);
    if (p === '/api/v2/models') return json({models: [], harnesses: [], enabled_providers: []});
    if (p === '/api/v2/operations') return json({machines: [], services: [], agents: []});
    if (p === '/api/v2/usage/limits') return json({bots: {}});
    if (p === '/api/v2/settings/history') return json({changes: [], transitions: []});
    if (p === '/api/status') return json({active: [], employees: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/goals/tree') return json({goals: [], owners: {}, other_kpis: [], proposals: []});
    if (p === '/api/v2/goals/needs-you') return json({actor: 'human:ana', items: []});
    return json(unrouted(p));
  });
  await page.goto('http://tico-ui.test/#/goals');
  await page.locator('#goal-tree .gt-row').first().waitFor();
  return {page, errors};
}
const goalLines = page => page.locator('#goal-tree > li').evaluateAll(els => els
  .map(el => `${el.dataset.owner_id || (el.classList.contains('gt-group') ? 'group:' + el.textContent.trim() : '')}@${el.style.getPropertyValue('--d')}`)
  .filter(l => l.startsWith('bot:') || l.startsWith('group:')));
// Every chart node above this one, nearest first.
const above = (page, key) => page.locator(`#tree [data-org="${key}"]`).first().evaluate(el => {
  const out = [];
  for (let li = el.closest('li')?.parentElement?.closest('li'); li; li = li.parentElement?.closest('li'))
    out.push(li.querySelector(':scope > .noderow [data-org], :scope > .noderow[data-org]')?.dataset.org
      || li.querySelector(':scope > .noderow')?.dataset.org || '');
  return out;
});
// The rows at the top level of the Team tree, in order.
const topRows = page => page.locator('#tree > li > .noderow').evaluateAll(rows => rows.map(row =>
  (row.querySelector('[data-org]') || row).dataset.org));

async function check(page, name = 'Strategy Bot') {
  // Off the Team chart, as the other built-ins are; the Head's department, the Head, its report and the bot with no
  // manager the server hung under it report to the owner, who is the top of the chart: they sit beside the owner's row,
  // the department first.
  assert.equal(await page.locator('#tree [data-org="b:goal-manager"]').count(), 0, 'the Strategy Bot is not on the Team tree');
  assert.deepEqual(await above(page, 'g:marketing'), [], 'the group the server hangs under it shows at the top, beside the owner');
  assert.deepEqual(await above(page, 'b:cmo'), ['g:marketing']);
  assert.deepEqual(await above(page, 'b:seo'), ['b:cmo', 'g:marketing']);
  assert.deepEqual(await above(page, 'b:scribe'), []);
  assert.deepEqual(await topRows(page), ['p:ana', 'g:marketing', 'b:scribe']);
  assert.equal(await page.locator('#tree a.node[data-org="b:coo"]').count(), 0, 'the Assistant stays off the chart');
  // The Goals tab's tree nests them the same way; Strategy has no chat with it, and where it is named it uses its name.
  assert.deepEqual(await goalLines(page), ['bot:scribe@1', 'group:Marketing@1', 'bot:cmo@2', 'bot:seo@3']);
  assert.equal(await page.locator('#sbot, .gm-form').count(), 0, 'no Strategy Bot chat');
  assert.equal(await page.evaluate(() => strategyBotName()), name);
  // The old names an install stored before the upgrade are never shown.
  assert.doesNotMatch(await page.locator('body').innerText(), /Goal Manager|CEO Bot/);
}
(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    // Its name, the name of a bot the upgrade has not reached yet (never shown), and a name the owner gave it.
    for (const [sent, shown] of [['CEO Bot', 'Strategy Bot'], ['Goal Manager', 'Strategy Bot'], ['Atlas', 'Atlas']]) {
      const {page, errors} = await open(browser, {name: sent});
      await check(page, shown);
      assert.deepEqual(errors, []);
      await page.close();
    }
    // The owner in a group of their own, with a bot that has no manager: what hung under the Strategy Bot hangs under
    // the owner, in the owner's group, at desktop and phone widths.
    for (const width of [1280, 390]) {
      const server = {home: true};
      const {page, errors} = await open(browser, server, width);
      assert.equal(await page.locator('#tree [data-org="b:goal-manager"]').count(), 0, `${width}: not on the Team tree`);
      assert.deepEqual(await above(page, 'b:scribe'), ['p:ana', 'g:home'], `${width}: Scribe under the owner`);
      assert.deepEqual(await above(page, 'g:marketing'), ['p:ana', 'g:home'], `${width}: Marketing under the owner`);
      assert.deepEqual(await above(page, 'b:seo'), ['b:cmo', 'g:marketing', 'p:ana', 'g:home']);
      assert.deepEqual(await goalLines(page), ['group:Home@0', 'bot:scribe@2', 'group:Marketing@2', 'bot:cmo@3', 'bot:seo@4']);
      assert.deepEqual(errors, []);
      await page.close();
    }
    // A person who chose Hide from my chart before it went: nothing was moved for them, and the chart reads the same,
    // with the bot that reports to the Strategy Bot itself under the owner. No Hide or Show on my chart anywhere: not on
    // the Team tree, not on its row in Settings > Bots, not in its editor; its row there still opens its page.
    {
      const server = {name: 'Atlas', analyst: true, hidden: true};
      const {page, errors} = await open(browser, server);
      await page.locator('#tree [data-org="b:cmo"]').waitFor();
      assert.equal(await page.locator('#tree [data-org="b:goal-manager"]').count(), 0);
      assert.deepEqual(await above(page, 'b:analyst'), [], 'a bot reporting to it hangs from the next row up, the owner at the top');
      assert.deepEqual(await topRows(page), ['p:ana', 'g:marketing', 'b:analyst', 'b:scribe']);
      assert.deepEqual(await above(page, 'b:cmo'), ['g:marketing']);
      assert.deepEqual(await above(page, 'b:scribe'), []);
      assert.equal(await page.evaluate(() => strategyBotName()), 'Atlas', 'it keeps its name');
      await page.evaluate(() => { location.hash = '#/settings'; });
      // The page change draws a frame later (its fade); Settings' data can already be loaded before it does.
      await page.waitForFunction(() => S.route === '#/settings' && typeof SETTINGS_DATA !== 'undefined' && SETTINGS_DATA.models);
      await page.evaluate(() => settingsShow('bots'));
      const row = page.locator('[data-settings-bot="goal-manager"]');
      await row.waitFor();
      assert.equal(await row.locator('a.sb-name').getAttribute('href'), '#/bot/goal-manager');
      assert.doesNotMatch(await row.innerText(), /chart/i);
      await page.evaluate(() => { settingsEditBot('goal-manager', true); $('#bot-editor').showModal(); });   // as its settings tab draws it
      await page.locator('#bot-editor input[name="display_name"]').waitFor();
      assert.doesNotMatch(await page.locator('#bot-editor').innerText(), /on your chart|from your chart|my chart/i);
      assert.equal(await page.locator('body').getByText(/Hide from my chart|Show on my chart/).count(), 0);
      assert.deepEqual(server.saves || [], [], 'no chart preference is written');
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log('strategy-bot: ok');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
