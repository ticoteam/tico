// A computer's internet shared with bots: in its pop-up the owner turns sharing on, picks bots and sets the daily cap,
// each change POSTing the whole setting; a refused save puts the switch back; today's sites load when opened. An
// admin who isn't the owner can only turn it off. A bot's "Browse through" offers only computers that share with it.
// Every request is intercepted.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    const page = await browser.newPage({viewport: {width: 1280, height: 800}, serviceWorkers: 'block'});
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    const exit = (more) => ({sharing: false, bots: [], daily_cap_bytes: 5e9, may_change: true, may_turn_off: true, used_today_bytes: 0, ...more});
    const computer = (id, label, operator, more) => ({id, label, operator, bots: [], platform: 'darwin', last_seen_at: new Date().toISOString(),
      readiness: {}, screen: 'off', screen_available: false, ...more});
    const machines = [computer('c1', 'Ana MacBook', 'ana', {browser_exit: exit()}),
      computer('c2', 'Ben laptop', 'ben', {browser_exit: exit({sharing: true, bots: ['ops'], may_change: false})})];
    const emps = [{name: 'ops', display_name: 'Ops', status: 'active', operator: 'ana', revision: 1},
      {name: 'web', display_name: 'Web', status: 'active', operator: 'ana', revision: 1}];
    const writes = [], botWrites = [], usage = [];
    let refuse = false;
    await page.route('**/*', route => {
      const req = route.request(), url = new URL(req.url()), p = url.pathname;
      const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
      if (ui && fs.existsSync(uiFile(ui[1])))
        return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
      if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      const config = {version: '0.1.0', app_name: 'Tico'};
      if (p === '/api/me') return json({id: 'ana', role: 'owner', name: 'Ana', email: 'ana@example.com', cloud: true, registered: true, config});
      if (p === '/api/v2/config') return json(config);
      if (p === '/api/employees') return json(emps);
      if (p === '/api/issues') return json([]);
      if (p === '/api/humans' || p === '/api/people') return json({people: [{id: 'ana', name: 'Ana'}, {id: 'ben', name: 'Ben'}], teams: {}});
      if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: []});
      if (p === '/api/v2/status') return json({bots: []});
      if (p === '/api/v2/needs-you') return json({items: []});
      if (p === '/api/v2/operations') return json({machines, services: []});
      const set = p.match(/^\/api\/v2\/computers\/(c\d)\/browser-exit$/);
      if (set && req.method() === 'POST') {
        if (refuse) return json({error: {code: 'forbidden', detail: 'Only the computer\'s owner can change this'}}, 403);
        const body = req.postDataJSON(); writes.push([set[1], body]);
        const m = machines.find(row => row.id === set[1]);
        m.browser_exit = {...m.browser_exit, ...body};
        return json(m.browser_exit);
      }
      if (p === '/api/v2/computers/c1/browser-exit/usage') {
        usage.push(url.searchParams.get('days'));
        return json({rows: [{day: '2026-11-01', bot: 'ops', host: 'www.acme.example', bytes: 2.5e6, tunnels: 3}],
                     days: [{day: '2026-11-01', bytes: 2.5e6}], next: null});
      }
      const bot = p.match(/^\/api\/v2\/bots\/(\w+)\/browser-exit$/);
      if (bot && req.method() === 'POST') {
        const body = req.postDataJSON(); botWrites.push([bot[1], body]);
        emps.find(e => e.name === bot[1]).browser_exit = body.runner_id;
        return json({browser_exit: body.runner_id && {runner_id: body.runner_id}});
      }
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      return json(unrouted(p));
    });
    await page.goto('https://tico-ui.test/#/settings');
    await page.locator('[data-settings-tab=devices]').click();
    await page.locator('#set-machines .machine-name[data-computer-open=c1]').click();
    const section = page.locator('#set-machines dialog[open] [data-detail-section=internet]');
    await section.waitFor();
    // Off: only the switch. On: the whole setting is saved, then bots and the cap appear.
    assert.equal(await section.locator('[data-browser-exit-bots]').count(), 0);
    await page.locator('[data-browser-exit=c1]').check();
    await section.locator('[data-browser-exit-bots=c1] input[value=ops]').waitFor();
    assert.deepEqual(writes.at(-1), ['c1', {sharing: true}]);
    await section.locator('[data-browser-exit-bots=c1] input[value=ops]').check();
    await page.waitForFunction(() => SETTINGS_DATA.machines[0].browser_exit.bots.includes('ops'));
    assert.deepEqual(writes.at(-1), ['c1', {sharing: true, bots: ['ops'], daily_cap_bytes: 5e9}]);
    await section.locator('[data-browser-exit-cap=c1]').fill('2.5');
    await section.locator('[data-browser-exit-cap=c1]').press('Tab');
    await page.waitForFunction(() => SETTINGS_DATA.machines[0].browser_exit.daily_cap_bytes === 2.5e9);
    // Today's sites, loaded on opening.
    await section.locator('[data-browser-exit-usage=c1] summary').click();
    await section.locator('.computer-exit-table td', {hasText: 'www.acme.example'}).waitFor();
    assert.deepEqual(usage, ['7']);
    // A refused save puts the switch back.
    refuse = true;
    await page.locator('[data-browser-exit=c1]').uncheck();
    await page.waitForFunction(() => document.querySelector('[data-browser-exit=c1]')?.checked && !document.querySelector('[data-browser-exit=c1]').disabled);
    refuse = false;
    await page.keyboard.press('Escape');
    // Ben's laptop: Ana may turn it off but not pick its bots or cap.
    await page.locator('#set-machines .machine-name[data-computer-open=c2]').click();
    await section.waitFor();
    assert.equal(await section.locator('[data-browser-exit-bots], [data-browser-exit-cap]').count(), 0);
    await page.locator('[data-browser-exit=c2]').uncheck();
    await page.waitForFunction(() => !SETTINGS_DATA.machines[1].browser_exit.sharing);
    assert.deepEqual(writes.at(-1), ['c2', {sharing: false}]);
    await page.keyboard.press('Escape');
    // Ops may browse through Ana's MacBook (it shares with ops); Web is offered nothing.
    const offered = await page.evaluate(() => [settingsBrowserExitRow(S.emps[0]), settingsBrowserExitRow(S.emps[1])]);
    assert.match(offered[0], /value="c1"/);
    assert.doesNotMatch(offered[0], /value="c2"/);
    assert.equal(offered[1], '');
    assert.deepEqual(errors, []);
    console.log('PASS: sharing switch, bots and cap saved; refused save reverts; usage loads; admin only turns off; Browse through choices');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
