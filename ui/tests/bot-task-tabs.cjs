// A bot's own task tabs (Task-list tabs 2/3, backend/bot_task_tabs.py): the tab settings add a "product tasks" tab of
// type Product roadmap with its own count and only those tasks; editing a tab's states moves its list and count; a
// built-in tab can be renamed, moved or removed; each bot keeps its own layout; and past the rail's width the tabs
// scroll sideways with every one still reachable, with no cap. Fixtures only, no network.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');

const ago = minutes => new Date(Date.now() - minutes * 60000).toISOString();
const bots = ['lead', 'qa'].map(name => ({name, display_name: {lead: 'Head of Engineering', qa: 'QA Engineer'}[name], org_parent: '',
  host: 'keeper', status: 'active', can_chat: true, users: [{id: 'ana', name: 'Ana'}], schedules: []}));
const task = (id, title, over = {}) => ({id, title, body: 'Details.', owner: 'bot:lead', requester: 'human:ana', status: 'doing',
  lane: 'company', rank: null, labels: [], tags: [], links: [], parts: {total: 0, done: 0}, version: 1, created: ago(600), updated: ago(60), ...over});
const TASKS = [
  task('t-own', 'Ship the release notes'),
  task('t-waits', 'Turn on the screen default', {status: 'waiting', waiting_on: 'human:ana', created: ago(480)}),
  task('t-on-qa', 'Merge the export fix', {status: 'waiting', waiting_on: 'bot:qa', created: ago(300)}),
  task('t-roadmap', 'Plan the import page', {status: 'open', type_id: 'roadmap', created: ago(30)}),
  task('t-roadmap-done', 'Write the import spec', {status: 'done', type_id: 'roadmap', done_at: ago(1440), created: ago(3 * 1440)}),
  task('t-roadmap-old', 'Retire the old import', {status: 'done', type_id: 'roadmap', done_at: ago(30 * 1440), created: ago(31 * 1440)}),
  task('t-qa', 'Run the release check', {owner: 'bot:qa', requester: 'bot:lead'}),
];
const TYPES = [{id: 'general', name: 'General', steps: []}, {id: 'roadmap', name: 'Product roadmap', role: 'product-roadmap', steps: []}];
const DEFAULT = [{builtin: 'needs', name: 'Needs You'}, {builtin: 'active', name: 'Doing'}, {builtin: 'blocked', name: 'Blocked'},
                 {builtin: 'done', name: 'Done Recently'}];

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    const page = await browser.newPage({viewport: {width: 1280, height: 900}, serviceWorkers: 'block'});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    // The server's layouts: one per bot. QA's has its own tabs already, and Ana may not change them.
    const layouts = {qa: [{builtin: 'active', name: 'Doing'}, {name: 'Waiting on bots', types: [], states: ['waiting'], waiting_on: 'bot'}]};
    const puts = [];
    await page.route('**/*', route => {
      const request = route.request(), url = new URL(request.url()), p = url.pathname, q = url.searchParams;
      const json = body => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
      if (/\/marked\.min\.js$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: 'window.marked={parse:s=>String(s)}'});
      if (url.origin !== 'https://tico-ui.test') return route.abort();
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
      if (ui && fs.existsSync(uiFile(ui[1]))) return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      if (p === '/api/me') return json({id: 'ana', role: 'owner', name: 'Ana', email: 'ana@example.test', cloud: true});
      if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana'}]});
      if (p === '/api/issues') return json([]);
      if (p === '/api/employees') return json(bots);
      if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: [], schedules: []});
      if (p === '/api/v2/status') return json({bots: bots.map(b => ({bot: b.name, state: 'idle'}))});
      if (p === '/api/v2/needs-you') return json({items: [], went_away: null});
      if (p === '/api/v2/goals') return json({goals: [], chain: [], reports: [], company: []});
      if (p === '/api/v2/updates') return json({updates: [], missed: [], unread: 0, next_before: null, today: {}});
      if (p === '/api/v2/task-types') return json({types: TYPES});
      const tabs = p.match(/^\/api\/v2\/bots\/([^/]+)\/task-tabs$/);
      if (tabs) {
        const bot = tabs[1];
        if (request.method() === 'PUT') {
          const body = JSON.parse(request.postData());
          puts.push({bot, tabs: body.tabs});
          if (body.tabs === null) delete layouts[bot]; else layouts[bot] = body.tabs;
        }
        return json({bot, tabs: layouts[bot] || DEFAULT, saved: !!layouts[bot], personal: false, can_edit: bot === 'lead'});
      }
      if (p === '/api/v2/tasks') {
        const who = q.get('owner') || q.get('requester'), field = q.get('owner') ? 'owner' : 'requester';
        return json({tasks: TASKS.filter(t => t[field] === 'bot:' + who && (field === 'owner' || t.status !== 'done'))});
      }
      if (p === '/api/v2/conversations') return json({conversations: []});
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      return json(unrouted(p));
    });
    const rail = page.locator('#pane-tasks');
    const tabTexts = async () => (await rail.locator('[data-bot-task-tab]').allInnerTexts()).map(s => s.replace(/\s+/g, ' ').trim());
    const shownTitles = async () => (await rail.locator('[role=tabpanel]:not([hidden]) .trow .ttl').allInnerTexts()).map(s => s.trim());
    const pick = name => rail.locator('[data-bot-task-tab]', {hasText: name}).click();
    const dialog = page.locator('#bot-task-tabs-dialog');
    const item = name => dialog.locator('.bot-tab-item').filter({has: page.locator(`input[data-tab-name]`).and(page.locator(`[value="${name}"]`))});

    await page.goto('https://tico-ui.test/#/bot/lead/tasks');
    await rail.locator('#t-open .trow').first().waitFor();
    assert.deepEqual(await tabTexts(), ['Needs You', 'Doing 4', 'Blocked 2', 'Done Recently 1']);
    await rail.locator('#bot-tasks-settings:not([hidden])').waitFor();

    // Test 6: add a tab of type Product roadmap, named "product tasks": it appears with its own count and only those tasks.
    await rail.locator('#bot-tasks-settings').click();
    await dialog.waitFor();
    assert.match(await dialog.innerText(), /Everyone who opens Head of Engineering sees these tabs/);
    assert.equal(await dialog.locator('.bot-tab-item').count(), 4);
    await dialog.getByRole('button', {name: 'Add a tab'}).click();
    const added = dialog.locator('.bot-tab-item').last();
    await added.locator('input[data-tab-name]').fill('product tasks');
    await added.getByLabel('Product roadmap').check();
    await dialog.getByRole('button', {name: 'Save'}).click();
    await dialog.waitFor({state: 'hidden'});
    assert.deepEqual(puts.at(-1), {bot: 'lead', tabs: [...DEFAULT,
      {name: 'product tasks', types: ['roadmap'], states: ['open', 'doing', 'waiting'], waiting_on: ''}]});
    assert.deepEqual(await tabTexts(), ['Needs You', 'Doing 4', 'Blocked 2', 'Done Recently 1', 'product tasks 1']);
    await pick('product tasks');
    assert.deepEqual(await shownTitles(), ['Plan the import page']);

    // Test 8: include Done in its states: the finished roadmap task of the last two weeks joins, list and count alike.
    await rail.locator('#bot-tasks-settings').click();
    await item('product tasks').getByLabel('Done', {exact: true}).check();
    // Rename a built-in tab, move "product tasks" up one and remove Blocked, in the same save.
    await item('Doing').locator('input[data-tab-name]').fill('Everything open');
    await item('product tasks').getByRole('button', {name: 'Move product tasks up'}).click();
    await item('Blocked').getByRole('button', {name: 'Remove Blocked'}).click();
    await dialog.getByRole('button', {name: 'Save'}).click();
    await dialog.waitFor({state: 'hidden'});
    assert.deepEqual(await tabTexts(), ['Needs You', 'Everything open 4', 'product tasks 2', 'Done Recently 1']);
    await pick('product tasks');
    assert.deepEqual(await shownTitles(), ['Plan the import page', 'Write the import spec']);
    await pick('Everything open');
    assert.equal((await shownTitles()).length, 4);

    // Test 7: QA keeps its own tabs; changing Head of Engineering's never moved them. Ana may not change QA's: no settings.
    assert.ok(puts.every(put => put.bot === 'lead'));
    await page.goto('https://tico-ui.test/#/bot/qa/tasks');
    await page.waitForFunction(() => document.querySelector('#bot-tab-t-c1')?.textContent.includes('Waiting on bots'));
    assert.deepEqual(await tabTexts(), ['Doing 1', 'Waiting on bots']);
    assert.equal(await rail.locator('#bot-tasks-settings').isHidden(), true);
    await page.goto('https://tico-ui.test/#/bot/lead/tasks');
    await page.waitForFunction(() => document.querySelector('#bot-tab-t-c2')?.textContent.includes('product tasks'));
    assert.deepEqual(await tabTexts(), ['Needs You', 'Everything open 4', 'product tasks 2', 'Done Recently 1']);

    // Test 9: far more tabs than fit. All are kept (no cap); the bar scrolls sideways and each tab can be reached.
    await rail.locator('#bot-tasks-settings').click();
    for (let n = 0; n < 12; n++) await dialog.getByRole('button', {name: 'Add a tab'}).click();
    await dialog.getByRole('button', {name: 'Save'}).click();
    await dialog.waitFor({state: 'hidden'});
    assert.equal(puts.at(-1).tabs.length, 16);
    assert.equal(await rail.locator('[data-bot-task-tab]').count(), 16);
    const bar = rail.locator('.bot-task-tabs');
    const fits = await bar.evaluate(el => ({wide: el.scrollWidth, shown: el.clientWidth, overflow: getComputedStyle(el).overflowX, wrap: getComputedStyle(el).flexWrap}));
    assert.ok(fits.wide > fits.shown * 2, JSON.stringify(fits));
    assert.equal(fits.overflow, 'auto');
    assert.equal(fits.wrap, 'nowrap');
    // A mouse wheel over the bar scrolls it sideways.
    await bar.hover();
    await page.mouse.wheel(0, 200);
    await page.waitForFunction(() => document.querySelector('#pane-tasks .bot-task-tabs').scrollLeft > 0);
    // End reaches the last tab and brings it into view; a click reaches any of them.
    await rail.locator('[data-bot-task-tab][aria-selected=true]').focus();
    await page.keyboard.press('End');
    const last = rail.locator('[data-bot-task-tab]').last();
    assert.equal(await last.getAttribute('aria-selected'), 'true');
    const inView = i => bar.evaluate((el, i) => { const t = el.children[i].getBoundingClientRect(), b = el.getBoundingClientRect();
      return t.left >= b.left - 1 && t.right <= b.right + 1; }, i);
    assert.ok(await inView(15), 'the last tab is in view');
    await page.keyboard.press('Home');
    assert.ok(await inView(0), 'and the first again');
    await rail.locator('[data-bot-task-tab]').nth(9).click();
    assert.equal(await rail.locator('[data-bot-task-tab]').nth(9).getAttribute('aria-selected'), 'true');

    // Reset goes back to the four built-in tabs.
    await rail.locator('#bot-tasks-settings').click();
    await dialog.getByRole('button', {name: 'Reset to the four built-in tabs'}).click();
    await dialog.waitFor({state: 'hidden'});
    assert.equal(puts.at(-1).tabs, null);
    assert.deepEqual(await tabTexts(), ['Needs You', 'Doing 4', 'Blocked 2', 'Done Recently 1']);
    assert.deepEqual(errors, []);
    console.log('bot-task-tabs: ok');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
