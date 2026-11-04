// Settings has no dead ends (settings stage 1). Fixtures only.
//  - every #/settings/<name> link the app draws opens a real tab: a name Settings doesn't know would land on General
//    with nothing said, which is how "Picks and services" once opened Updates;
//  - #/settings/gateway opens Gateway, #/settings/models opens Computers with the Models card in view, and the two links
//    between them go there;
//  - while a tab is shown the address bar names it (#/settings/<tab>), so it can be copied or shared: opening an address
//    shows its tab's own, picking another tab follows without a history step, a reload reopens it, and a redraw
//    (route() called again, as a status change does) keeps Settings and the tab; the old Rules and Routines
//    addresses open System;
//  - a first visit opens General; a tab this person can't see opens General with "That page is for admins", and an
//    unknown one opens General without it.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');

// Names the router or settingsShow takes besides the tabs and their aliases (ui/app/router.js, settingsShow).
const SPECIAL = ['models', 'providers', 'tags', 'types', 'triggers', 'runs', 'roadmap', 'company-inbox', 'subscriptions',
  'storage', 'team', 'company', 'assistant', 'privacy'];
// Old names that leave Settings: Listening moved to Strategy › Market (ui/app/redesign.js; redesign-shell.cjs checks it),
// and the old Strategy Bot address only redirects to its tab (ui/app/router.js), so no link is drawn to it.
const MOVED = ['listening', 'ceo'];

// Every #/settings/<name> address written in the UI's sources.
function settingsLinks() {
  const ui = path.join(__dirname, '..');
  const files = [...fs.readdirSync(ui).filter(f => f.endsWith('.js')).map(f => path.join(ui, f)),
    ...fs.readdirSync(path.join(ui, 'app')).filter(f => f.endsWith('.js')).map(f => path.join(ui, 'app', f))];
  const names = new Set();
  for (const file of files) for (const m of fs.readFileSync(file, 'utf8').matchAll(/#\/settings\/([a-z-]+)/g)) names.add(m[1]);
  return [...names].filter(n => !MOVED.includes(n)).sort();
}

async function open(browser, {role = 'owner', hash = '#/settings'} = {}) {
  const context = await browser.newContext({viewport: {width: 1280, height: 900}, serviceWorkers: 'block'});
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
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
      can_manage: admin, owner_id: 'ana', users: [{id: 'ana', name: 'Ana'}], schedules: [], revision: 1}]);
    if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: []});
    if (p === '/api/v2/status') return json({bots: [{bot: 'ops', state: 'idle'}]});
    if (p === '/api/v2/billing') return json({state: 'no_gateway'});
    if (p.endsWith('/watch') || p === '/api/v2/events') return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
  await page.goto('https://tico-ui.test/' + hash);
  await page.locator('#settings-tabs').waitFor();
  return {page, context, errors};
}
const selected = page => page.locator('#settings-tabs [aria-selected=true]').getAttribute('data-settings-tab');
// Opens an address and waits for Settings to write its tab's address back (an address already in the bar reroutes).
const go = async (page, hash) => {
  await page.evaluate(h => { SETTINGS_ADDRESS = ''; if (location.hash === h) route(); else location.hash = h; }, hash);
  await page.waitForFunction(() => S.route === SETTINGS && SETTINGS_ADDRESS && location.hash === SETTINGS_ADDRESS);
};
const address = page => page.evaluate(() => location.hash);

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    {
      // A first visit opens General.
      const {page, context, errors} = await open(browser);
      assert.equal(await selected(page), 'general', 'Settings opens on General');
      assert.equal(await page.locator('#settings-denied').isVisible(), false);

      // Every link the app draws to a Settings address opens a tab Settings knows, for the owner, with no "admins" line.
      const known = await page.evaluate(() => ({tabs: SETTINGS_TABS, alias: Object.keys(SETTINGS_ALIAS)}));
      for (const name of settingsLinks()) {
        assert.ok(known.tabs.includes(name) || known.alias.includes(name) || SPECIAL.includes(name),
          `#/settings/${name} is linked in the app but Settings has no such tab`);
        await go(page, `#/settings/${name}`);
        assert.equal(await page.locator('#settings-denied').isVisible(), false, `#/settings/${name} opens for the owner`);
        if (known.tabs.includes(name)) assert.equal(await selected(page), name, `#/settings/${name} opens that tab`);
      }

      // Gateway and Models, by address and by the links between them.
      await go(page, '#/settings/gateway');
      assert.equal(await selected(page), 'gateway');
      await go(page, '#/settings/models');
      assert.equal(await selected(page), 'models');
      await page.locator('#settings-models [data-gateway-advanced] > summary').click();
      await page.locator('#settings-models a[href="#/settings/gateway"]').click();
      await page.waitForFunction(() => location.hash === '#/settings/gateway' && SETTINGS_TAB === 'gateway');
      await page.locator('[data-gateway-key-link]').click();
      await page.waitForFunction(() => location.hash === '#/settings/models' && SETTINGS_TAB === 'models');
      await page.waitForFunction(() => { const r = document.querySelector('#settings-models')?.getBoundingClientRect(); return r && r.top < innerHeight && r.bottom > 0; });

      // A Settings address survives a refresh in a new tab: the address, not the last tab, decides.
      const fresh = await context.newPage();
      await fresh.goto('https://tico-ui.test/#/settings/system');
      await fresh.waitForFunction(() => typeof SETTINGS_TAB !== 'undefined' && SETTINGS_TAB === 'system' && location.hash === '#/settings/system');
      await fresh.close();
      assert.deepEqual(errors, []);
      await context.close();
    }
    {
      // The address bar names the tab shown, so it can be copied, bookmarked or shared.
      const {page, context, errors} = await open(browser, {hash: '#/settings/gateway'});
      await page.waitForFunction(() => SETTINGS_TAB === 'gateway');
      assert.equal(await address(page), '#/settings/gateway', 'an opened tab keeps its address');
      const steps = await page.evaluate(() => history.length);
      await page.locator('#settings-tabs [data-settings-tab=bots]').click();
      await page.waitForFunction(() => location.hash === '#/settings/bots');
      assert.equal(await selected(page), 'bots');
      assert.equal(await page.evaluate(() => history.length), steps, 'a tab change adds no history step');
      await page.reload();
      await page.locator('#settings-tabs [data-settings-tab=bots][aria-selected=true]').waitFor();
      assert.equal(await address(page), '#/settings/bots', 'a reload reopens the same tab');
      // Other names and sections show the address of the tab they open.
      await go(page, '#/settings/providers');
      assert.equal(await address(page), '#/settings/models');
      await go(page, '#/settings/devices/settings-models');
      assert.equal(await address(page), '#/settings/models', 'the old Models card on Computers opens Models');
      await go(page, '#/settings/computers');
      assert.equal(await address(page), '#/settings/devices');
      // The old Rules and Routines addresses open System, and a redraw keeps Settings and the tab.
      for (const old of ['#/settings/rules', '#/settings/rules?task=t1', '#/settings/routines', '#/settings/recurring']) {
        await go(page, old);
        assert.equal(await address(page), '#/settings/system', old);
      }
      await page.evaluate(() => route());
      assert.deepEqual(await page.evaluate(() => [S.route, SETTINGS_TAB, location.hash]), ['#/settings', 'system', '#/settings/system']);
      // Leaving Settings and coming back by Back reopens the tab the address names.
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
      // A tab this person can't see opens General and says why; one that doesn't exist opens General quietly.
      const {page, context, errors} = await open(browser, {role: 'member', hash: '#/settings/people'});
      await page.waitForFunction(() => SETTINGS_TAB === 'general');
      assert.equal(await selected(page), 'general');
      assert.equal(await page.locator('#settings-denied').isVisible(), true);
      assert.match(await page.locator('#settings-denied').innerText(), /That page is for admins/);
      assert.equal(await address(page), '#/settings/general', 'the address names the tab shown, not the one refused');
      await go(page, '#/settings/nothing-here');
      assert.equal(await selected(page), 'general');
      assert.equal(await page.locator('#settings-denied').isVisible(), false, 'an unknown address says nothing about admins');
      assert.deepEqual(errors, []);
      await context.close();
    }
  } finally { await browser.close(); }
  console.log('settings-no-dead-ends ok');
})().catch(error => { console.error(error); process.exit(1); });
