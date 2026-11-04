// Settings > System > Strategy Bot (ui/app/settings-strategy-bot.js), a helper's card beside the Task Manager's: the owner sees the regular checks, on, and the three levels with
// their sentences, Suggests actions current (the level every install starts at); picking another shows its sentence
// again and changes nothing until Confirm, and Cancel leaves it as it was. Turning the regular checks off saves at
// once. Only the owner has the card: an admin or a member has none, and nobody has a tab or a chat for it. The card and
// headings use the bot's name. Fixtures only.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile, harnesses} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');

const LEVELS = [
  {level: 1, name: 'Passive', sentence: 'It keeps the regular checks running and otherwise acts only when asked through your Assistant.', ready: true},
  {level: 2, name: 'Suggests actions', sentence: 'It also asks goal owners and suggests how your bots could recover a goal.', ready: true},
  {level: 3, name: 'Acts on its own', sentence: 'It also decides on its own: it makes, moves up, reassigns and closes bot tasks, each with an Undo.', ready: true},
];

async function open(browser, {role = 'owner', name = 'Strategy Bot'} = {}) {
  const page = await browser.newPage({viewport: {width: 1440, height: 900}, serviceWorkers: 'block'});
  const errors = [], posted = [];
  page.on('pageerror', e => errors.push(e.message));
  const state = {regular_checks: true, level: 2, level_chosen: false, level_changed_by: '', level_changed_by_name: '', level_changed_at: '', level_history: [], levels: LEVELS,
    name, asked_today: {}};
  await page.route('**/*', route => {
    const req = route.request(), p = new URL(req.url()).pathname;
    const json = body => route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify(body)});
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1])))
      return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
    if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const config = {version: '0.1.0', app_name: 'Tico'};
    if (p === '/api/me') return json({id: role === 'owner' ? 'ana' : 'cara', role, name: 'Ana', email: 'ana@example.com', cloud: true, registered: true, config});
    if (p === '/api/v2/config') return json(config);
    if (p === '/api/employees') return json([{name: 'goal-manager', display_name: name, status: 'active'}]);
    if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/models') return json({models: [], harnesses});
    if (p === '/api/v2/strategy-bot' && req.method() === 'GET') return json(state);
    if (p === '/api/v2/strategy-bot/level') {
      const body = req.postDataJSON();
      posted.push(body);
      state.level_chosen = true;
      // The same level again is only kept as the owner's choice: nothing changed, so no history line.
      if (body.level === state.level) return json(state);
      const at = new Date().toISOString();
      state.level_history.unshift({level: body.level, was: state.level, by: 'human:ana', by_name: 'Ana', at});
      Object.assign(state, {level: body.level, level_changed_by: 'human:ana', level_changed_by_name: 'Ana', level_changed_at: at});
      return json(state);
    }
    if (p === '/api/v2/strategy-bot/checks') {
      const body = req.postDataJSON();
      posted.push({checks: body.on});
      state.regular_checks = body.on;
      return json(state);
    }
    // The Strategy Bot log, two pages: Undo on an entry of the second page.
    const entry = (id, over) => ({id, kind: 'decision', reason: 'Reason ' + id, state: 'done', undoable: true, created_at: '2026-10-01T00:00:00Z',
      done: [{text: 'Close "Task ' + id + '"'}], left: [], note: '', proposal_task: null, undo: null, ...over});
    if (p === '/api/v2/strategy-bot/log') {
      const before = new URL(req.url()).searchParams.get('before');
      return json(before === 'c|first-last' ? {items: [entry('older')], next_before: null, can_undo: true}
        : {items: [entry('newest'), entry('first-last')], next_before: 'c|first-last', can_undo: true});
    }
    const undo = p.match(/^\/api\/v2\/strategy-bot\/log\/([^/]+)\/undo$/);
    if (undo) {
      posted.push({undo: undo[1]});
      return json(entry(undo[1], {state: 'undone', undoable: false, undo: {restored: [], kept: [], text: 'Undone: Task ' + undo[1] + ' put back.'}}));
    }
    if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
  await page.goto('https://tico-ui.test/#/settings');
  await page.locator('#settings-tabs').waitFor();
  return {page, errors, posted};
}

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    {
      // Old settings addresses (#/settings/ceo, #/settings/strategy-bot, in links and bookmarks) open its card in System.
      const {page, errors} = await open(browser);
      await page.evaluate(() => { location.hash = '#/settings/ceo'; });
      await page.locator('#set-strategy-bot [data-strategy-level]').first().waitFor();
      assert.match(await page.evaluate(() => location.hash), /^#\/settings\/system/);
      assert.equal(await page.locator('[data-settings-tab="strategy-bot"]').count(), 0, 'no tab of its own');
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      const {page, errors, posted} = await open(browser);
      await page.evaluate(() => { location.hash = '#/settings/system'; });   // System sits under More
      const levels = page.locator('#set-strategy-bot [data-strategy-level]');
      await levels.first().waitFor();
      assert.equal(await levels.count(), 3);
      assert.match(await levels.nth(1).innerText(), /Suggests actions[\s\S]*Now[\s\S]*asks goal owners/);
      assert.equal(await levels.nth(1).getAttribute('aria-checked'), 'true');
      assert.match(await levels.nth(2).innerText(), /decides on its own/);
      const panel = await page.locator('#set-strategy-bot').innerText();
      assert.match(panel,
        /Regular checks[\s\S]*How much your Strategy Bot does[\s\S]*Suggests actions, where every company starts/);
      assert.doesNotMatch(panel, /Goal Manager|level 1|switch/i);
      assert.equal(await page.locator('#set-strategy-bot [data-strategy-checks]').isChecked(), true);
      // The level is only the default, so the owner can confirm it as their own choice; once chosen, picking it again
      // asks nothing.
      await levels.nth(1).click();
      await page.locator('#set-strategy-bot .strategy-confirm', {hasText: 'Keep Suggests actions as your choice?'}).waitFor();
      await page.locator('#set-strategy-bot [data-strategy-confirm="2"]').click();
      await page.locator('#set-strategy-bot .strategy-confirm').waitFor({state: 'detached'});
      assert.deepEqual(posted, [{level: 2}]);
      await levels.nth(1).click();
      await page.waitForTimeout(200);
      assert.equal(await page.locator('#set-strategy-bot .strategy-confirm').count(), 0);
      posted.length = 0;
      // Picking Passive shows its sentence with a Confirm; Cancel changes nothing.
      await levels.nth(0).click();
      const confirm = page.locator('#set-strategy-bot .strategy-confirm');
      await confirm.waitFor();
      assert.match(await confirm.innerText(), /Change to Passive\?[\s\S]*acts only when asked through your Assistant/);
      await confirm.locator('[data-strategy-cancel]').click();
      await confirm.waitFor({state: 'detached'});
      assert.deepEqual(posted, []);
      // Confirm saves it, and the change is listed with who.
      await levels.nth(0).click();
      await page.locator('#set-strategy-bot [data-strategy-confirm="1"]').click();
      await page.waitForFunction(() =>
        document.querySelector('#set-strategy-bot [data-strategy-level="1"]')?.classList.contains('cur'));
      assert.deepEqual(posted, [{level: 1}]);
      assert.match(await page.locator('#set-strategy-bot').innerText(), /chosen by Ana/);
      // The regular checks go off at once, on the owner's click.
      await page.locator('#set-strategy-bot [data-strategy-checks]').uncheck();
      await page.waitForFunction(() => document.querySelector('#set-strategy-bot [data-strategy-checks]') && !document.querySelector('#set-strategy-bot [data-strategy-checks]').disabled
        && !document.querySelector('#set-strategy-bot [data-strategy-checks]').checked);
      assert.deepEqual(posted, [{level: 1}, {checks: false}]);
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // Named by the owner: the card and the heading use that name.
      const {page, errors} = await open(browser, {name: 'Atlas'});
      await page.evaluate(() => { location.hash = '#/settings/system'; });   // System sits under More
      await page.locator('#set-strategy-bot [data-strategy-level]').first().waitFor();
      assert.equal((await page.locator('#set-strategy-bot-card h2').innerText()).trim(), 'Atlas');
      assert.match(await page.locator('#set-strategy-bot').innerText(), /How much your Atlas does/);
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // Undo on an entry past the first page asks, and confirms, where it is: the older page stays loaded.
      const {page, errors, posted} = await open(browser);
      await page.evaluate(() => { const host = document.createElement('div'); host.id = 'log-under-test'; host.style.cssText = 'position:fixed;inset:0;z-index:99999;background:#fff;overflow:auto'; document.body.append(host); void renderStrategyBotLog(host); });
      const log = page.locator('#log-under-test');
      await log.locator('[data-strategy-log-more]').click();
      await log.locator('[data-strategy-undo="older"]').click();
      await log.locator('[data-strategy-undo-confirm="older"]').click();
      await log.getByText('Undone: Task older put back.').waitFor();
      assert.deepEqual(posted, [{undo: 'older'}]);
      assert.equal(await log.locator('[data-strategy-log]').count(), 3);
      assert.equal(await log.locator('[data-strategy-undo]').count(), 2);
      assert.deepEqual(errors, []);
      await page.close();
    }
    for (const opts of [{role: 'admin'}, {role: 'viewer'}]) {
      const {page, errors} = await open(browser, opts);
      // Even its old address shows no card for them (a member may not see System at all).
      await page.evaluate(() => { location.hash = '#/settings/strategy-bot'; });
      await page.waitForFunction(() => !location.hash.endsWith('/strategy-bot'));
      assert.equal(await page.locator('#set-strategy-bot-card, [data-settings-tab="strategy-bot"]').count(), 0, JSON.stringify(opts));
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log('settings-strategy-bot: ok');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
