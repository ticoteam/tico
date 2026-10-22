// Settings > Routines (ui/app/settings-recurring.js): a System section above the bot routines lists every built-in job
// by group with its schedule, last run in words (a failure in red with its reason), next run, and for a Decide job the
// route that answered, a fallback in words and this month's spend. Housekeeping says Tico needs it. No controls.
// A member sees the same list. Fixtures only.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile, harnesses} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');

const soon = new Date(Date.now() + 40 * 60000).toISOString(), before = new Date(Date.now() - 20 * 60000).toISOString();
const job = (key, name, extra = {}) => ({key, name, what: name + ' does a thing.', schedule: 'Hourly', ai: false,
  controllable: false, needed: false, records: true, last: null, next: null, ...extra});
const SYSTEM = {now: new Date().toISOString(), groups: [
  {key: 'checks', name: 'Checks that suggest', jobs: [
    job('stale_review', 'Stale ticket review', {ai: true, controllable: true, route: 'Gateway', fallback: null,
      spent_this_month_usd: 0.42, next: soon, last: {started: before, finished: before, ok: true, result: '9 checked, 2 suggestions', runs: 1}}),
    job('still_wanted', 'Still wanted?', {controllable: true, schedule: 'Daily', next: soon,
      last: {started: before, finished: before, ok: false, result: 'Failed: the database was busy', runs: 1}})]},
  {key: 'learning', name: 'Learning', jobs: [
    job('learnings', 'Nightly learnings', {ai: true, controllable: true, schedule: 'Nightly at 03:00', route: 'Company key',
      fallback: 'Ran on your own key: Gateway unavailable', spent_this_month_usd: 1.5, next: soon,
      last: {started: before, finished: before, ok: true, result: '40 read, 3 bots sent learnings', runs: 1}})]},
  {key: 'reminders', name: 'Reminders and deadlines', jobs: [job('due_reminders', 'Due reminders', {schedule: 'Every 10 seconds'})]},
  {key: 'housekeeping', name: 'Housekeeping', jobs: [job('stall_checks', 'Stall checks', {needed: true, schedule: 'Every minute'})]},
]};

async function open(browser, role) {
  const page = await browser.newPage({viewport: {width: 1440, height: 900}, serviceWorkers: 'block'});
  const errors = [], asked = [];
  page.on('pageerror', e => errors.push(e.message));
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
    if (p === '/api/employees') return json([{name: 'ops', display_name: 'Ops', status: 'active'}]);
    if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: [],
      schedules: [{id: 's1', title: 'Weekly report', employee: 'ops', bot: 'ops', cron: '0 9 * * 1', active: true, next: soon}]});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/models') return json({models: [], harnesses});
    if (p === '/api/v2/system-routines') { asked.push(req.method()); return json(SYSTEM); }
    if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
  await page.goto('https://tico-ui.test/#/settings');
  await page.locator('#settings-tabs').waitFor();
  return {page, errors, asked};
}

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    for (const role of ['owner', 'human']) {
      const {page, errors, asked} = await open(browser, role);
      await page.locator('#settings-more summary').click();
      await page.locator('[data-settings-tab="recurring"]').click();
      await page.locator('[data-system-routine="stale_review"]').waitFor();
      // Above the bot routines, four groups in order.
      const headings = await page.locator('#settings-recurring h2').allInnerTexts();
      assert.deepEqual(headings.map(h => h.trim()), ['System', 'Bot routines'], role);
      assert.deepEqual(await page.locator('#set-system-routines .sysr-group h3').evaluateAll(els => els.map(el => el.textContent)),
        ['Checks that suggest', 'Learning', 'Reminders and deadlines', 'Housekeeping']);
      assert.equal(await page.locator('#set-system-routines .sysr-group').count(), 4);
      const stale = await page.locator('[data-system-routine="stale_review"]').innerText();
      assert.match(stale, /Stale ticket review[\s\S]*AI: Gateway[\s\S]*\$0\.42 this month[\s\S]*Hourly[\s\S]*9 checked, 2 suggestions[\s\S]*20m ago[\s\S]*in 40m/);
      const failed = page.locator('[data-system-routine="still_wanted"] [data-sysr-result]');
      assert.equal(await failed.innerText(), 'Failed: the database was busy');
      assert.equal(await failed.evaluate(el => el.classList.contains('sysr-failed')), true);
      assert.notEqual(await failed.evaluate(el => getComputedStyle(el).color), await page.locator('[data-system-routine="stale_review"] [data-sysr-result]').evaluate(el => getComputedStyle(el).color), 'a failure is red');
      assert.equal(await page.locator('[data-system-routine="learnings"] [data-sysr-fallback]').innerText(), 'Ran on your own key: Gateway unavailable');
      assert.match(await page.locator('[data-system-routine="stall_checks"]').innerText(), /Tico needs this to run/);
      assert.doesNotMatch(await page.locator('[data-system-routine="stale_review"]').innerText(), /Tico needs this to run/);
      assert.match(await page.locator('[data-system-routine="due_reminders"]').innerText(), /Not run yet/);
      // Read-only: no switch, no schedule choice, no Run now.
      assert.equal(await page.locator('#set-system-card input, #set-system-card select, #set-system-card button').count(), 0);
      // The bot routines are still listed below.
      assert.match(await page.locator('#set-recurring').innerText(), /Weekly report/);
      assert.deepEqual(asked, ['GET']);
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // A phone: the row stacks and nothing spills sideways.
      const {page} = await open(browser, 'owner');
      await page.setViewportSize({width: 390, height: 844});
      await page.locator('#settings-more summary').click();
      await page.locator('[data-settings-tab="recurring"]').click();
      await page.locator('[data-system-routine="stale_review"]').waitFor();
      const wide = await page.evaluate(() => document.querySelector('#set-system-routines').scrollWidth - document.querySelector('#set-system-routines').clientWidth);
      assert.ok(wide <= 1, `no sideways scroll on a phone (${wide}px)`);
      await page.close();
    }
    console.log('settings-system-routines: ok');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
