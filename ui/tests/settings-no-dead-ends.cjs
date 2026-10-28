// Settings has no dead ends (settings stages 1 and 2). Fixtures only.
//  - desktop shows the groups in a list down the left (You, a divider, then the company's ten) with no More menu; a
//    phone shows them as one picker that reaches every group (stage 2, criteria 18 and 19);
//  - every #/settings/<name> link the app draws opens a real group: a name Settings doesn't know would land on You
//    with nothing said, which is how "Picks and services" once opened Updates;
//  - every address Settings had before the groups opens what it opened, in the group that holds it now, scrolled to
//    its card (criterion 26), and so does a settings catalogue link naming a card that moved;
//  - #/settings/gateway opens AI models at the Gateway card, and the two links between it and Models stay in that group;
//  - while a group is shown the address bar names it (#/settings/<group>), so it can be copied or shared: opening an
//    address shows its group's own, picking another group follows without a history step, a reload reopens it, and a
//    redraw (route() called again, as a routine change does) keeps Settings and the rules narrowed by ?task=;
//  - a first visit opens You; a group or card this person can't see opens You with "That page is for admins", and an
//    unknown one opens You without it;
//  - a routine's On switch shows on its row without expanding, and a refused change puts the server's reason in red on
//    that row and turns the switch back, with no browser alert.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');

// Names the router takes besides the groups and the old tabs settingsShow maps (ui/app/router.js).
const SPECIAL = ['roadmap'];
// Every address Settings had before the groups: the group it opens now and the card it scrolls to ('' when it opens at
// the group's top). The old tabs (General, People, Bots, CEO Bot, Computers, Repositories, Tasks, My Google account,
// Email & Phone, Rules, Webhooks, Listening, Health, History, Gateway, Billing), their other names, and General's old
// sections.
const OLD = {
  general: ['you', ''], assistant: ['you', '#settings-assistant-pane'], inbox: ['you', '#settings-inbox'], google: ['you', '#settings-inbox'],
  people: ['people', ''], humans: ['people', ''],
  bots: ['bots', ''],
  models: ['models', ''], providers: ['models', ''], chat: ['models', ''], gateway: ['models', '#settings-gateway'],
  devices: ['computers', ''], computers: ['computers', ''], subscriptions: ['computers', ''],
  billing: ['limits', '#settings-billing'],
  recurring: ['automation', ''], rules: ['automation', ''], routines: ['automation', '#set-routines-card'],
  listening: ['automation', '#settings-listening'],
  tasks: ['tasks', ''], tags: ['tasks', '#settings-tags'], types: ['tasks', '#settings-types'], roadmap: ['tasks', ''],
  repos: ['connections', '#settings-repos'], repositories: ['connections', '#settings-repos'],
  inbound: ['connections', '#settings-inbound'], 'email-phone': ['connections', '#settings-inbound'],
  'company-inbox': ['connections', '#settings-inbound'],
  webhooks: ['connections', '#settings-webhooks'], triggers: ['connections', '#settings-webhooks'],
  'webhooks/outgoing': ['connections', '#settings-webhooks'], 'webhooks/gateway': ['connections', '#settings-webhooks'],
  team: ['policies', '#settings-team'], company: ['policies', '#settings-company'], privacy: ['policies', '#settings-privacy'],
  storage: ['policies', '#settings-storage'],
  health: ['health', ''], history: ['health', '#settings-history'], runs: ['health', '#settings-history'],
};
// The settings catalogue's links name the old tab and the card (backend/settings_entries.py): the card decides.
const CARDS = {'devices/settings-tokens': 'connections', 'devices/settings-models': 'models', 'bots/settings-turn-budgets': 'limits',
  'general/settings-storage': 'policies', 'recurring/set-system-card': 'automation', 'general/settings-assistant-pane': 'you'};
const GROUPS = ['you', 'people', 'bots', 'models', 'computers', 'limits', 'automation', 'tasks', 'connections', 'policies', 'health'];

// Every #/settings/<name> address written in the UI's sources.
function settingsLinks() {
  const ui = path.join(__dirname, '..');
  const files = [...fs.readdirSync(ui).filter(f => f.endsWith('.js')).map(f => path.join(ui, f)),
    ...fs.readdirSync(path.join(ui, 'app')).filter(f => f.endsWith('.js')).map(f => path.join(ui, 'app', f))];
  const names = new Set();
  for (const file of files) for (const m of fs.readFileSync(file, 'utf8').matchAll(/#\/settings\/([a-z-]+)/g)) names.add(m[1]);
  return [...names].sort();
}

async function open(browser, {role = 'owner', hash = '#/settings', refuse = false, phone = false} = {}) {
  const context = await browser.newContext({viewport: phone ? {width: 390, height: 844} : {width: 1280, height: 900}, serviceWorkers: 'block'});
  const page = await context.newPage();
  const errors = [], dialogs = [], writes = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('dialog', d => { dialogs.push(d.message()); void d.dismiss(); });
  const admin = role === 'owner';
  await context.route('**/*', route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname;
    const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (/\/marked\.min\.js$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: 'window.marked={parse:s=>String(s)}'});
    if (url.origin !== 'https://tico-ui.test') return route.abort();
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1])))
      return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
    if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const config = {version: '0.1.0', app_name: 'Tico'};
    if (p === '/api/me') return json({id: admin ? 'ana' : 'cara', role: admin ? 'owner' : 'human', name: admin ? 'Ana' : 'Cara',
      email: admin ? 'ana@acme.example' : 'cara@acme.example', cloud: true, registered: true, config});
    if (p === '/api/v2/config') return json(config);
    if (p === '/api/humans' || p === '/api/people') return json({people: [{id: 'ana', name: 'Ana'}, {id: 'cara', name: 'Cara'}], teams: {}});
    if (p === '/api/employees') return json([{name: 'ops', display_name: 'Ops', status: 'active', host: 'keeper', can_chat: true,
      can_manage: admin, operator: 'ana', users: [{id: 'ana', name: 'Ana'}], schedules: [], revision: 1}]);
    if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: [],
      schedules: [{id: 'r1', employee: 'ops', title: 'Morning brief', cron: '30 7 * * 1-5', timezone: 'America/Los_Angeles',
        enabled: true, active: true, next: new Date(Date.now() + 3600e3).toISOString()}]});
    if (p === '/api/v2/status') return json({bots: [{bot: 'ops', state: 'idle'}]});
    if (p === '/api/v2/routines/r1' && req.method() === 'POST') {
      writes.push(req.postDataJSON());
      if (refuse) return json({error: {detail: 'Ops is paused: resume it before turning its routines on or off', code: 'conflict'}}, 409);
      return json({id: 'r1', enabled: req.postDataJSON().enabled});
    }
    // What the tabs the links open read first, empty.
    if (p === '/api/v2/listening/destinations') return json({destinations: [], categories: [], receivers: []});
    if (p === '/api/v2/billing') return json({state: 'no_gateway'});
    if (p === '/api/v2/ceo-bot') return json(null, 404);
    if (p.endsWith('/watch') || p === '/api/v2/events') return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
  await page.goto('https://tico-ui.test/' + hash);
  await page.locator('#settings-tabs').waitFor();
  return {page, context, errors, dialogs, writes};
}
const selected = page => page.locator('#settings-tabs [aria-selected=true]').getAttribute('data-settings-tab');
// Opens an address and waits for Settings to write its group's address back (an address already in the bar reroutes).
const go = async (page, hash) => {
  await page.evaluate(h => { SETTINGS_ADDRESS = ''; if (location.hash === h) route(); else location.hash = h; }, hash);
  await page.waitForFunction(() => S.route === SETTINGS && SETTINGS_ADDRESS && location.hash === SETTINGS_ADDRESS);
};
const address = page => page.evaluate(() => location.hash);

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    {
      // A first visit opens You. Desktop lists the groups down the left, You above a divider, and has no More menu.
      const {page, context, errors} = await open(browser);
      assert.equal(await selected(page), 'you', 'Settings opens on You');
      assert.equal(await page.locator('#settings-denied').isVisible(), false);
      assert.deepEqual(await page.locator('#settings-tabs .settings-group-list [data-settings-tab]').evaluateAll(b => b.map(x => x.dataset.settingsTab)), GROUPS);
      assert.deepEqual(await page.locator('#settings-tabs .settings-group-list button:visible').allInnerTexts(),
        ['You', 'People', 'Bots', 'AI models', 'Computers', 'Limits & billing', 'Automation', 'Tasks', 'Connections', 'Company & policies', 'Health & history']);
      assert.equal(await page.locator('#settings-tabs .settings-group-list > [data-settings-tab="you"] + hr').count(), 1, 'a divider under You');
      assert.equal(await page.locator('#settings-more, #settings-tabs details').count(), 0, 'no More menu');
      assert.equal(await page.locator('#settings-group-pick').isVisible(), false, 'no picker on desktop');
      const box = await page.locator('#settings-tabs').boundingBox(), panes = await page.locator('.settings-panes').boundingBox();
      assert.ok(box.x + box.width <= panes.x, 'the list sits left of the groups');

      // Every link the app draws to a Settings address opens a group Settings knows, for the owner, with no "admins" line.
      const known = await page.evaluate(() => SETTINGS_TABS);
      for (const name of settingsLinks()) {
        assert.ok(known.includes(name) || SPECIAL.includes(name), `#/settings/${name} is linked in the app but Settings has no such place`);
        await go(page, `#/settings/${name}`);
        assert.equal(await page.locator('#settings-denied').isVisible(), false, `#/settings/${name} opens for the owner`);
        if (GROUPS.includes(name)) assert.equal(await selected(page), name, `#/settings/${name} opens that group`);
      }

      // Every pre-group address opens what it opened, in its new group, and scrolls to its card.
      for (const [name, [group, card]] of Object.entries(OLD)) {
        await go(page, `#/settings/${name}`);
        assert.equal(await selected(page), group, `#/settings/${name} opens ${group}`);
        assert.equal(await page.locator('#settings-denied').isVisible(), false, `#/settings/${name} opens for the owner`);
        assert.equal(await address(page), `#/settings/${group}`, `#/settings/${name} shows its group's address`);
        if (!card) continue;
        assert.equal(await page.locator(`#settings-group-${group} ${card}`).count(), 1, `${card} sits in ${group}`);
        await page.waitForFunction(sel => { const r = document.querySelector(sel)?.getBoundingClientRect(); return r && r.height > 0 && r.top < innerHeight && r.bottom > 0; }, card);
      }
      for (const [link, group] of Object.entries(CARDS)) {
        await go(page, `#/settings/${link}`);
        assert.equal(await selected(page), group, `#/settings/${link} opens the group its card moved to`);
      }

      // Gateway and Models, by address and by the links between them: one group now.
      await go(page, '#/settings/gateway');
      assert.equal(await selected(page), 'models');
      await page.locator('#settings-models').waitFor();
      await page.locator('#settings-models a[href="#/settings/gateway"]').click();
      await page.waitForFunction(() => location.hash === '#/settings/models' && SETTINGS_TAB === 'models');
      await page.locator('[data-gateway-key-link]').click();
      await page.waitForFunction(() => location.hash === '#/settings/models' && SETTINGS_TAB === 'models');
      await page.waitForFunction(() => { const r = document.querySelector('#settings-models')?.getBoundingClientRect(); return r && r.top < innerHeight && r.bottom > 0; });

      // A Settings address survives a refresh in a new tab: the address, not the last group, decides.
      const fresh = await context.newPage();
      await fresh.goto('https://tico-ui.test/#/settings/recurring');
      await fresh.waitForFunction(() => typeof SETTINGS_TAB !== 'undefined' && SETTINGS_TAB === 'automation' && location.hash === '#/settings/automation');
      await fresh.close();
      assert.deepEqual(errors, []);
      await context.close();
    }
    {
      // A phone shows one picker instead of the list, and every group can be reached from it.
      const {page, context, errors} = await open(browser, {phone: true, hash: '#/settings/billing'});
      const pick = page.locator('#settings-group-pick');
      assert.equal(await pick.isVisible(), true, 'the picker shows on a phone');
      assert.equal(await page.locator('#settings-tabs .settings-group-list').isVisible(), false, 'the list does not');
      await page.waitForFunction(() => SETTINGS_TAB === 'limits');
      assert.equal(await pick.inputValue(), 'limits', 'the picker names the group an address opened');
      assert.deepEqual(await pick.locator('option').evaluateAll(o => o.map(x => x.value)), GROUPS);
      for (const group of GROUPS) {
        await pick.selectOption(group);
        await page.waitForFunction(g => SETTINGS_TAB === g && location.hash === '#/settings/' + g, group);
        assert.equal(await page.locator(`#settings-group-${group}`).isVisible(), true, `${group} shows from the picker`);
        assert.equal(await page.locator('.settings-pane[data-settings-group]:visible').count(), 1, 'one group at a time');
      }
      assert.deepEqual(errors, []);
      await context.close();
    }
    {
      // The address bar names the group shown, so it can be copied, bookmarked or shared.
      const {page, context, errors} = await open(browser, {hash: '#/settings/models'});
      await page.waitForFunction(() => SETTINGS_TAB === 'models');
      assert.equal(await address(page), '#/settings/models', 'an opened group keeps its address');
      const steps = await page.evaluate(() => history.length);
      await page.locator('#settings-tabs [data-settings-tab=bots]').click();
      await page.waitForFunction(() => location.hash === '#/settings/bots');
      assert.equal(await selected(page), 'bots');
      assert.equal(await page.evaluate(() => history.length), steps, 'a group change adds no history step');
      await page.reload();
      await page.locator('#settings-tabs [data-settings-tab=bots][aria-selected=true]').waitFor();
      assert.equal(await address(page), '#/settings/bots', 'a reload reopens the same group');
      // Other names and sections show the address of the group they open.
      await go(page, '#/settings/devices');
      assert.equal(await address(page), '#/settings/computers');
      await go(page, '#/settings/rules');
      assert.equal(await address(page), '#/settings/automation');
      // Rules narrowed to one task keep that in the address, and a redraw keeps Settings, the group and the narrowing.
      await go(page, '#/settings/rules?task=t1');
      assert.equal(await address(page), '#/settings/rules?task=t1');
      await page.evaluate(() => route());
      assert.deepEqual(await page.evaluate(() => [S.route, SETTINGS_TAB, RULES_FOR, location.hash]),
        ['#/settings', 'automation', 'task:t1', '#/settings/rules?task=t1']);
      // Leaving Settings and coming back by Back reopens the group the address names.
      await page.evaluate(() => { location.hash = '#/settings/health'; });
      await page.waitForFunction(() => location.hash === '#/settings/health' && SETTINGS_TAB === 'health');
      await page.evaluate(() => { location.hash = '#/updates'; });
      await page.waitForFunction(() => S.route === '#/updates');
      await page.goBack();
      await page.waitForFunction(() => S.route === SETTINGS && SETTINGS_TAB === 'health' && location.hash === '#/settings/health');
      assert.deepEqual(errors, []);
      await context.close();
    }
    {
      // A group or card this person can't see opens You and says why; one that doesn't exist opens You quietly.
      const {page, context, errors} = await open(browser, {role: 'member', hash: '#/settings/people'});
      await page.waitForFunction(() => SETTINGS_TAB === 'you');
      assert.equal(await selected(page), 'you');
      assert.equal(await page.locator('#settings-denied').isVisible(), true);
      assert.match(await page.locator('#settings-denied').innerText(), /That page is for admins/);
      assert.equal(await address(page), '#/settings/you', 'the address names the group shown, not the one refused');
      for (const name of ['billing', 'gateway', 'listening', 'storage', 'policies']) {
        await go(page, `#/settings/${name}`);
        assert.equal(await selected(page), 'you', `#/settings/${name} is for admins`);
        assert.equal(await page.locator('#settings-denied').isVisible(), true);
      }
      await go(page, '#/settings/nothing-here');
      assert.equal(await selected(page), 'you');
      assert.equal(await page.locator('#settings-denied').isVisible(), false, 'an unknown address says nothing about admins');
      assert.deepEqual(errors, []);
      await context.close();
    }
    {
      // A routine's switch is on its row; a refused change shows the reason on that row, in red, and no alert.
      const {page, context, errors, dialogs, writes} = await open(browser, {hash: '#/settings/recurring', refuse: true});
      const on = page.locator('#set-recurring [data-routine-on="r1"]');
      await on.waitFor();
      assert.equal(await page.locator('#set-recurring .rocc').first().isVisible(), false, 'without expanding the row');
      assert.equal(await on.isChecked(), true);
      await on.click();
      const line = page.locator('#set-recurring [data-field-err]');
      await line.waitFor();
      assert.match(await line.innerText(), /resume it before/);
      assert.equal(await line.evaluate(el => el.classList.contains('err')), true, 'the line is the red error style');
      assert.equal(await on.isChecked(), true, 'the switch goes back');
      assert.deepEqual(writes, [{enabled: false}]);
      assert.deepEqual(dialogs, [], 'no browser alert');
      assert.deepEqual(errors, []);
      await context.close();
    }
  } finally { await browser.close(); }
  console.log('settings-no-dead-ends ok');
})().catch(error => { console.error(error); process.exit(1); });
