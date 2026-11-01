// A bot's editable sidebar (backend/bot_task_tabs.py, ui/app/bot-sidebar.js): until the layout arrives the rail says
// Loading; one pencil (for someone who may edit) opens edit mode in place, with Done and Cancel. There each widget
// can be moved by its handle, hidden with its eye and given a height by its bottom edge (with Reset); every task tab is
// a pill that can be dragged, opened (Name, Show count, conditions, Delete, Save) or added with +. Done saves it all in
// one PUT; Cancel drops it. A hidden widget is not drawn outside edit mode; Tasks stops at 500px unless set. The layout
// is kept in this browser and drawn from there at once on the next visit. Someone who may not edit has no pencil.
// Fixtures only, no network.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');

const ago = minutes => new Date(Date.now() - minutes * 60000).toISOString();
const bots = ['lead', 'qa'].map(name => ({name, display_name: {lead: 'Head of Engineering', qa: 'QA Engineer'}[name], org_parent: '',
  host: 'keeper', status: 'active', can_chat: true, users: [{id: 'ana', name: 'Ana'}], schedules: []}));
const task = (id, title, over = {}) => ({id, title, body: 'Details.', owner: 'bot:lead', requester: 'human:ana', status: 'doing',
  lane: 'company', rank: null, labels: [], tags: [], links: [], parts: {total: 0, done: 0}, version: 1, created_at: ago(600), updated_at: ago(60), ...over});
const TASKS = [
  task('t-own', 'Ship the release notes'),
  task('t-waits', 'Turn on the screen default', {status: 'waiting', waiting_on: 'human:ana', created_at: ago(480)}),
  task('t-bot-wait', 'Wait for QA', {status: 'waiting', waiting_on: null, note: 'Waiting for the test machine', relations: {blocks: [{id: 't-qa', title: 'Run the release check', owner: 'bot:qa', status: 'doing', direction: 'in'}]}, blocked_reason: 'Waiting for the test machine', blocked_since: ago(480)}),
  task('t-roadmap', 'Plan the import page', {status: 'open', type_id: 'roadmap', created_at: ago(30)}),
  task('t-roadmap-done', 'Write the import spec', {status: 'done', type_id: 'roadmap', done_at: ago(1440), created_at: ago(3 * 1440)}),
  task('t-qa', 'Run the release check', {owner: 'bot:qa', requester: 'bot:lead'}),
];
const TYPES = [{id: 'general', name: 'General', steps: []}, {id: 'roadmap', name: 'Product roadmap', role: 'product-roadmap', steps: []}];
const OTHERS = {name: 'Assigned to Others', show_count: true, conditions: [{field: 'asked_by', value: 'this_bot'},
  {field: 'assigned_to', value: 'others'}, {field: 'status', value: ['open', 'doing', 'waiting']}]};
const DEFAULT = [{builtin: 'needs', name: 'Needs You', show_count: true}, {builtin: 'active', name: 'Doing', show_count: true},
                 {builtin: 'blocked', name: 'Blocked', show_count: true}, OTHERS];
const CONDITIONS = {
  needs: [{field: 'needs', value: 'you'}],
  active: [{field: 'assigned_to', value: 'this_bot'}, {field: 'status', value: ['open', 'doing']}],
  blocked: [{field: 'assigned_to', value: 'this_bot'}, {field: 'status', value: ['waiting']}, {field: 'waiting_on', value: 'not_me'}],
};
const normalized = tab => tab.builtin && tab.builtin !== 'done' && !tab.conditions ? {...tab, conditions: CONDITIONS[tab.builtin]} : tab;
const WIDGETS = ['tasks', 'updates', 'files'].map(id => ({id, hidden: false, height: null}));
const PAGES = {'page:f1': {file_id: 'f1', title: 'Weekly numbers', bot: 'lead', updated_at: ago(90), archived: false},
  'page:f2': {file_id: 'f2', title: 'Release plan', bot: 'lead', updated_at: ago(30), archived: false}};
// The server's filter, for the fields these fixtures use; Ana is the viewer.
const matches = (bot, tab, t) => (t.owner === 'bot:' + bot || t.requester === 'bot:' + bot) && tab.conditions.every(({field, value}) => {
  const who = value === 'this_bot' ? 'bot:' + bot : value === 'me' ? 'human:ana' : value;
  if (field === 'status') return value.includes(t.status);
  if (field === 'needs') return (t.owner === 'human:ana' && ['open', 'doing', 'waiting'].includes(t.status)) || (t.status === 'waiting' && t.waiting_on === 'human:ana');
  if (field === 'waiting_on') {
    const blockers = (t.relations?.blocks || []).filter(r => r.direction === 'in' && !['done', 'closed', 'declined'].includes(r.status));
    const person = String(t.waiting_on || '').startsWith('human:');
    const botWait = !person && !['gate', 'worktree'].includes(t.blocked_kind) && !t.ask?.to_actor?.startsWith('human:')
      && !blockers.some(r => r.owner?.startsWith('human:')) && blockers[0]?.owner?.startsWith('bot:');
    return t.status === 'waiting' && (value === 'me' ? t.waiting_on === 'human:ana' : value === 'not_me' ? t.waiting_on !== 'human:ana'
      : value === 'person' ? person : value === 'bot' ? botWait : true);
  }
  if (field === 'type') return value.includes(t.type_id || 'general');
  if (field === 'asked_by') return t.requester === who;
  if (field === 'assigned_to') return value === 'others' ? !['human:ana', 'bot:' + bot].includes(t.owner) : t.owner === who;
  throw new Error('fixture has no ' + field);
});

async function open(browser, viewport, colorScheme, server) {
  const context = await browser.newContext({viewport, colorScheme, serviceWorkers: 'block'});
  // The app is dark unless this browser saved another theme (ui/theme.js).
  await context.addInitScript(theme => localStorage.setItem('tico.theme', theme), colorScheme);
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url()), p = url.pathname, q = url.searchParams;
    const json = body => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
    if (/\/marked\.min\.js$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: 'window.marked={parse:s=>String(s)}'});
    if (url.origin !== 'https://tico-ui.test') return route.abort();
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1]))) return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
    if (p === '/vendor/fonts/material-symbols-outlined.woff2') return route.fulfill({contentType: 'font/woff2', path: uiFile('vendor/fonts/material-symbols-outlined.woff2')});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    if (p === '/api/me') return json({id: 'ana', role: 'owner', name: 'Ana', email: 'ana@example.test', cloud: true});
    if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana'}]});
    if (p === '/api/issues') return json([]);
    if (p === '/api/employees') return json(bots);
    if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: [], schedules: []});
    if (p === '/api/v2/status') return json({bots: bots.map(b => ({bot: b.name, state: 'idle'}))});
    if (p === '/api/v2/needs-you') return json({items: [], went_away: null});
    if (p === '/api/v2/goals') return json({goals: [], chain: [], reports: [], company: []});
    if (p === '/api/v2/updates') return json({updates: q.get('bot') === 'lead' ? [{id: 'u1', bot: 'lead', day: '2026-10-23', kind: 'daily',
      body: 'Shipped the export fix.', read: true, created_at: ago(600)}] : [], missed: [], unread: 0, next_before: null, today: {}});
    if (p === '/api/v2/task-types') return json({types: TYPES});
    const layout = p.match(/^\/api\/v2\/bots\/([^/]+)\/task-tabs$/);
    if (layout) {
      const bot = layout[1];
      if (request.method() === 'PUT') {
        const body = JSON.parse(request.postData());
        server.puts.push({bot, body});
        if (server.putHold) await server.putHold;
        if (server.putFail && server.putFail--) return route.fulfill({status: 500, contentType: 'application/json', body: '{"detail":"Could not save."}'});
        server.layouts[bot] = {tabs: body.tabs, widgets: body.widgets};
      } else if (server.hold) await server.hold;
      const kept = server.layouts[bot] || {tabs: DEFAULT, widgets: WIDGETS};
      return json({bot, ...kept, saved: !!server.layouts[bot], personal: false, can_edit: bot === 'lead'});
    }
    const filtered = p.match(/^\/api\/v2\/bots\/([^/]+)\/task-tabs\/tasks$/);
    if (filtered) {
      const bot = filtered[1], tabs = (server.layouts[bot] || {tabs: DEFAULT}).tabs.map((tab, at) => ({tab: normalized(tab), key: tab.builtin || 'c' + at})).filter(({tab}) => tab.builtin !== 'done');
      // A page of server.page rows; `before` here is the index the next page starts at.
      if (q.get('tab') && server.moreHold) await server.moreHold;
      const from = Number(q.get('before') || 0), wanted = tabs.filter(({key}) => !q.get('tab') || key === q.get('tab'));
      const counted = wanted.map(({tab, key}) => {
        const all = (server.tasks || TASKS).filter(t => matches(bot, tab, t)).map(t => t.id), ids = all.slice(from, from + server.page);
        return {key, name: tab.name, count: all.length, ids, next: from + server.page < all.length ? String(from + server.page) : null};
      });
      return json({bot, tabs: counted, tasks: (server.tasks || TASKS).filter(t => counted.some(c => c.ids.includes(t.id)))});
    }
    if (p === '/api/v2/tasks') {
      const who = q.get('owner') || q.get('requester'), field = q.get('owner') ? 'owner' : 'requester';
      const status = q.get('status');
      return json({tasks: (server.tasks || TASKS).filter(t => t[field] === 'bot:' + who && (!status || status === 'all' || status.split(',').includes(t.status)))});
    }
    if (p === '/api/v2/conversations') return json({conversations: []});
    // The sidebar's pages (backend/cards.py): what GET /api/v2/places/bot:<slug> answers for the saved widgets.
    const pinned = decodeURIComponent(p).match(/^\/api\/v2\/places\/bot:([^/]+)$/);
    if (pinned) return json({place: 'bot:' + pinned[1], kind: 'bot', saved: true, can_edit: pinned[1] === 'lead',
      pins: ((server.layouts[pinned[1]] || {}).widgets || []).filter(w => PAGES[w.id]).map((w, i) => ({id: 'p' + i, item: w.id, position: i, size: 'medium', page: PAGES[w.id]}))});
    if (p === '/api/v2/cards/choices') return json({templates: [], teams: [], kpis: [], pages: Object.values(PAGES)});
    if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
  return {context, page, errors};
}

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    for (const [scheme, viewport] of [['light', {width: 1280, height: 900}], ['dark', {width: 375, height: 800}]]) {
      const phone = viewport.width < 760, at = `${scheme} ${viewport.width}`;
      let release;
      const server = {layouts: {}, puts: [], page: 1, hold: new Promise(resolve => { release = resolve; })};
      const {context, page, errors} = await open(browser, viewport, scheme, server);
      const rail = page.locator('#pane-tasks');
      const tabTexts = async () => (await rail.locator('[data-bot-task-tab]').allInnerTexts()).map(s => s.replace(/\s+/g, ' ').trim());
      const order = () => rail.evaluate(el => [...el.querySelectorAll(':scope>.bot-w')].map(w => w.dataset.widget));
      const widget = id => rail.locator(`.bot-w[data-widget="${id}"]`);
      const pill = name => rail.locator('[data-bot-task-tab]', {hasText: name});
      const dialog = page.locator('#bot-filter-dialog');
      const shows = () => rail.evaluate(el => [...el.querySelectorAll(':scope>.bot-w')].filter(w => w.offsetParent).map(w => w.dataset.widget));
      const goto = async hash => {
        await page.goto('https://tico-ui.test/' + hash);
        if (phone) await page.locator('.bot-switch button', {hasText: 'Tasks'}).click().catch(() => {});
      };

      // Loading until the layout arrives: no widget is drawn and then moved.
      await goto('#/bot/lead');
      await page.waitForFunction(() => document.querySelector('#pane-tasks')?.hasAttribute('data-loading'));
      if (phone) await page.locator('.bot-switch button', {hasText: 'Tasks'}).click();
      await rail.locator('.bot-rail-loading').waitFor();
      assert.deepEqual(await shows(), [], at + ': nothing drawn while the layout loads');
      release();
      await page.waitForFunction(() => document.querySelector('#cnt-c3')?.textContent.trim() === '1');
      assert.equal(await rail.locator('.bot-rail-loading').isHidden(), true);
      assert.deepEqual(await tabTexts(), ['Needs You 1', 'Doing 2', 'Blocked 1', 'Assigned to Others 1'], at);
      await pill('Blocked').click();
      assert.deepEqual(await rail.locator('#t-blocked .ttl').allInnerTexts(), ['Wait for QA']);
      assert.match(await rail.locator('#t-blocked .trow-waits').innerText(), /Waiting for the test machine.*since/);
      await pill('Doing').click();
      // Rows come a page (here one) at a time; the counts above already leave the blocked task out of Doing.
      assert.deepEqual(await rail.locator('#t-open .ttl').allInnerTexts(), ['Ship the release notes'], at);
      assert.deepEqual(await order(), ['tasks', 'updates', 'files']);
      await pill('Assigned to Others').click();
      assert.deepEqual(await rail.locator('#t-c3 .trow .ttl').allInnerTexts(), ['Run the release check']);
      // One pencil, no gear or board icon.
      await rail.locator('#bot-sidebar-edit:not([hidden])').waitFor();
      assert.equal(await page.locator('#bot-tasks-settings').count(), 0);
      assert.equal(await rail.locator('#bot-tasks-board').count(), 0);
      assert.equal(await rail.locator('.bot-rail-editbar').isHidden(), true);
      assert.equal(await widget('tasks').locator('[data-w-grip]').isHidden(), true, 'no handles outside edit mode');
      // Tasks stops at 500px by default and a short list stays short.
      const tasksMax = await rail.locator('#bot-tasks').evaluate(el => [getComputedStyle(el).maxHeight, el.getBoundingClientRect().height]);
      assert.equal(tasksMax[0], '500px', at);
      assert.ok(tasksMax[1] < 500, JSON.stringify(tasksMax));

      // Edit, change things, Cancel: nothing is written and the rail is as it was.
      await rail.locator('#bot-sidebar-edit').click();
      await rail.locator('#bot-sidebar-done').waitFor();
      assert.equal(await rail.locator('#bot-sidebar-edit').isHidden(), true);
      await widget('files').locator('[data-w-eye]').click();
      await pill('Doing').click();
      await dialog.waitFor();
      await dialog.locator('[data-filter-name]').fill('Busy');
      await dialog.getByRole('button', {name: 'Save'}).click();
      assert.deepEqual((await tabTexts()).slice(0, 2), ['Needs You 1', 'Busy 2']);
      await rail.locator('#bot-sidebar-cancel').click();
      assert.equal(server.puts.length, 0);
      assert.deepEqual(await tabTexts(), ['Needs You 1', 'Doing 2', 'Blocked 1', 'Assigned to Others 1']);
      assert.equal(await widget('files').getAttribute('data-off'), null);

      // Edit again: hide Files, move Updates up by keyboard and down past Files by dragging its handle, set Tasks' height
      // then Reset it, set Updates' height by dragging its edge.
      await rail.locator('#bot-sidebar-edit').click();
      assert.equal(await rail.evaluate(el => el.classList.contains('editing')), true);
      assert.equal(await rail.locator('[data-bot-tab-add]').isVisible(), true);
      const eye = widget('files').locator('[data-w-eye]');
      await eye.click();
      assert.equal(await eye.getAttribute('aria-pressed'), 'true');
      assert.equal(await eye.getAttribute('aria-label'), 'Show Files');
      assert.equal(await widget('files').locator('.bot-w-bar').isVisible(), true, 'a hidden widget stays in edit mode, to show again');
      await widget('updates').locator('[data-w-grip]').focus();
      await page.keyboard.press('ArrowUp');
      assert.deepEqual(await order(), ['updates', 'tasks', 'files']);
      const grip = await widget('updates').locator('[data-w-grip]').boundingBox(), last = await widget('files').boundingBox();
      await page.mouse.move(grip.x + grip.width / 2, grip.y + grip.height / 2);
      await page.mouse.down();
      await page.mouse.move(grip.x + grip.width / 2, grip.y + 40, {steps: 4});
      await page.mouse.move(grip.x + grip.width / 2, last.y + last.height - 2, {steps: 8});
      await page.mouse.up();
      assert.deepEqual(await order(), ['tasks', 'files', 'updates']);
      const size = widget('tasks').locator('[data-w-size]');
      await size.focus();
      await page.keyboard.press('ArrowUp');
      assert.ok(Number(await widget('tasks').getAttribute('data-h')) > 0);
      await widget('tasks').locator('[data-w-reset]').click();
      assert.equal(await widget('tasks').getAttribute('data-h'), null);
      assert.equal(await widget('tasks').locator('[data-w-reset]').isHidden(), true);
      const edge = await widget('updates').locator('[data-w-size]').boundingBox();
      const before = await widget('updates').locator('.rail-sec').evaluate(el => el.getBoundingClientRect().height);
      await page.mouse.move(edge.x + edge.width / 2, edge.y + edge.height / 2);
      await page.mouse.down();
      await page.mouse.move(edge.x + edge.width / 2, edge.y + edge.height / 2 + 300, {steps: 5});
      await page.mouse.up();
      const updatesHeight = Number(await widget('updates').getAttribute('data-h'));
      assert.ok(Math.abs(updatesHeight - Math.max(80, Math.round(before + 300))) <= 2, `${before} -> ${updatesHeight}`);

      // A default tab has ordinary editable conditions and Reset. Turn Doing’s count off.
      await pill('Doing').click();
      await dialog.waitFor();
      assert.deepEqual(await dialog.locator('[data-cond-field]').evaluateAll(els => els.map(el => el.value)), ['assigned_to', 'status']);
      assert.equal(await dialog.locator('[data-cond-value="0"]').inputValue(), 'this_bot');
      assert.deepEqual(await dialog.locator('[data-cond-value="1"]:checked').evaluateAll(els => els.map(el => el.value)), ['open', 'doing']);
      await dialog.locator('[data-cond-remove="0"]').click();
      await dialog.locator('[data-cond-add]').click();
      assert.equal(await dialog.locator('[data-cond-field]').count(), 2);
      await dialog.getByRole('button', {name: 'Reset to default'}).click();
      assert.deepEqual(await dialog.locator('[data-cond-field]').evaluateAll(els => els.map(el => el.value)), ['assigned_to', 'status']);
      assert.equal(await dialog.locator('[data-filter-count]').isChecked(), true);
      await dialog.locator('[data-filter-count]').uncheck();
      await dialog.getByRole('button', {name: 'Save'}).click();
      await dialog.waitFor({state: 'hidden'});
      assert.equal(await pill('Doing').locator('.cnt').isHidden(), true);
      // + adds a filter: name it, Type is Product roadmap, Status adds Done. Show count is on by default.
      await rail.locator('[data-bot-tab-add]').click();
      await dialog.waitFor();
      assert.equal(await dialog.locator('[data-filter-count]').isChecked(), true);
      await dialog.getByRole('button', {name: 'Save'}).click();
      assert.equal(await dialog.locator('[data-filter-error]').innerText(), 'Name it.');
      await dialog.locator('[data-filter-name]').fill('product tasks');
      await dialog.locator('[data-cond-value="0"][value="done"]').check();
      await dialog.locator('[data-cond-add]').click();
      await dialog.locator('[data-cond-field="1"]').selectOption('type');
      await dialog.locator('[data-cond-value="1"][value="general"]').uncheck();
      await dialog.locator('[data-cond-value="1"][value="roadmap"]').check();
      await dialog.locator('[data-cond-add]').click();
      await dialog.locator('[data-cond-field="2"]').selectOption('created_within');
      await dialog.locator('[data-cond-remove="2"]').click();
      await dialog.getByRole('button', {name: 'Save'}).click();
      await dialog.waitFor({state: 'hidden'});
      // Drag it to the front; ArrowLeft and ArrowRight move the focused pill too.
      // The bar is wider than the rail: held near its left end, it scrolls to the first tab.
      await pill('product tasks').scrollIntoViewIfNeeded();
      const from = await pill('product tasks').boundingBox(), bar = await rail.locator('.bot-task-tabs').boundingBox();
      await page.mouse.move(from.x + from.width / 2, from.y + from.height / 2);
      await page.mouse.down();
      await page.mouse.move(bar.x + 6, from.y + from.height / 2, {steps: 10});
      for (let n = 0; n < 40; n++) await page.mouse.move(bar.x + 4 + n % 2, from.y + from.height / 2);
      await page.mouse.up();
      assert.equal(await dialog.isVisible(), false, 'a drag does not open the pop-up');
      assert.deepEqual((await tabTexts()).map(s => s.replace(/ \d+$/, '')), ['product tasks', 'Needs You', 'Doing', 'Blocked', 'Assigned to Others']);
      await pill('Blocked').focus();
      await page.keyboard.press('ArrowRight');
      await pill('Blocked').focus();
      await page.keyboard.press('ArrowLeft');
      assert.deepEqual((await tabTexts()).map(s => s.replace(/ \d+$/, '')), ['product tasks', 'Needs You', 'Doing', 'Blocked', 'Assigned to Others']);
      // Delete removes a tab.
      await pill('Assigned to Others').click();
      await dialog.locator('[data-filter-delete]').click();
      await dialog.waitFor({state: 'hidden'});

      // Done: one PUT with everything. A failed write keeps the draft and edit mode, and the draft still changes.
      const files = widget('files').locator('[data-w-eye]'), filesHidden = () => files.getAttribute('aria-pressed');
      server.putFail = 1;
      await rail.locator('#bot-sidebar-done').click();
      await rail.locator('#bot-sidebar-status .err').waitFor();
      assert.equal(await rail.evaluate(el => el.classList.contains('editing')), true);
      await files.click();
      assert.equal(await filesHidden(), 'false');
      await files.click();
      assert.equal(await filesHidden(), 'true');
      // While the retry is on its way nothing in the draft changes: eye, Escape, a pill.
      let putDone;
      server.putHold = new Promise(resolve => { putDone = resolve; });
      await rail.locator('#bot-sidebar-done').click();
      await page.waitForFunction(() => document.querySelector('#bot-sidebar-status').textContent === 'Saving…');
      await files.dispatchEvent('click');
      assert.equal(await filesHidden(), 'true', 'no change while saving');
      assert.equal(await files.isDisabled(), true);
      await widget('tasks').locator('[data-w-size]').focus();
      await page.keyboard.press('ArrowDown');
      assert.equal(await widget('tasks').getAttribute('data-h'), null);
      await page.keyboard.press('Escape');
      await pill('Doing').dispatchEvent('click');
      assert.equal(await dialog.isVisible(), false);
      assert.equal(await rail.evaluate(el => el.classList.contains('editing')), true);
      putDone();
      server.putHold = null;
      await rail.locator('#bot-sidebar-edit:not([hidden])').waitFor();
      assert.equal(server.puts.length, 2);
      assert.deepEqual(server.puts[1], server.puts[0]);
      assert.deepEqual(server.puts[1], {bot: 'lead', body: {
        tabs: [{name: 'product tasks', show_count: true, conditions: [{field: 'status', value: ['open', 'doing', 'waiting', 'done']}, {field: 'type', value: ['roadmap']}]},
               {builtin: 'needs', name: 'Needs You', show_count: true, conditions: CONDITIONS.needs}, {builtin: 'active', name: 'Doing', show_count: false, conditions: CONDITIONS.active},
               {builtin: 'blocked', name: 'Blocked', show_count: true, conditions: CONDITIONS.blocked}],
        widgets: [{id: 'tasks', hidden: false, height: null},
                  {id: 'files', hidden: true, height: null}, {id: 'updates', hidden: false, height: updatesHeight}]}});
      assert.equal(await rail.evaluate(el => el.classList.contains('editing')), false);
      await page.waitForFunction(() => document.querySelector('#cnt-c0')?.textContent.trim() === '2');
      assert.deepEqual(await tabTexts(), ['product tasks 2', 'Needs You 1', 'Doing', 'Blocked 1']);
      await pill('product tasks').click();
      // A page at a time: Show more reads the next, and a refresh keeps as many as were read, also a refresh that
      // starts while Show more is on its way.
      assert.deepEqual(await rail.locator('#t-c0 .trow .ttl').allInnerTexts(), ['Plan the import page']);
      let moreDone;
      server.moreHold = new Promise(resolve => { moreDone = resolve; });
      await rail.locator('#t-c0 [data-bot-filter-more]').click();
      await page.evaluate(() => { window.refreshing = botTaskFilterRead('lead'); });
      await page.waitForTimeout(300);
      moreDone();
      server.moreHold = null;
      await page.evaluate(() => window.refreshing);
      await rail.locator('#t-c0 [data-bot-filter-more]').waitFor({state: 'detached'});
      assert.deepEqual(await rail.locator('#t-c0 .trow .ttl').allInnerTexts(), ['Plan the import page', 'Write the import spec']);
      await page.evaluate(() => botTaskFilterRead('lead'));
      assert.deepEqual(await rail.locator('#t-c0 .trow .ttl').allInnerTexts(), ['Plan the import page', 'Write the import spec']);
      assert.equal(await rail.locator('#t-c0 [data-bot-filter-more]').count(), 0);
      // A default tab uses exactly its edited conditions after Done and reload; Reset restores its full default.
      await rail.locator('#bot-sidebar-edit').click();
      await pill('Blocked').click();
      await dialog.waitFor();
      assert.equal(await dialog.locator('[data-cond-field]').count(), 3);
      assert.equal(await dialog.locator('[data-cond-value="2"]').inputValue(), 'not_me');
      await page.evaluate(() => document.fonts.ready);
      await page.screenshot({path: `/tmp/dev511-blocked-${scheme}-${viewport.width}.png`});
      await dialog.locator('[data-cond-remove="2"]').click();
      await dialog.locator('[data-cond-value="1"][value="waiting"]').uncheck();
      await dialog.locator('[data-cond-value="1"][value="open"]').check();
      await dialog.getByRole('button', {name: 'Save'}).click();
      await rail.locator('#bot-sidebar-done').click();
      await rail.locator('#bot-sidebar-edit:not([hidden])').waitFor();
      await goto('#/bot/lead');
      await page.waitForFunction(() => document.querySelector('#cnt-blocked')?.textContent === '1');
      await pill('Blocked').click();
      assert.deepEqual(await rail.locator('#t-blocked .trow .ttl').allInnerTexts(), ['Plan the import page']);
      await rail.locator('#bot-sidebar-edit').click();
      await pill('Blocked').click();
      await dialog.waitFor();
      assert.equal(await dialog.locator('[data-cond-field]').count(), 2);
      await dialog.getByRole('button', {name: 'Reset to default'}).click();
      assert.equal(await dialog.locator('[data-cond-field]').count(), 3);
      await dialog.getByRole('button', {name: 'Save'}).click();
      await pill('Needs You').click();
      await dialog.waitFor();
      assert.equal(await dialog.locator('[data-cond-field]').inputValue(), 'needs');
      assert.equal(await dialog.locator('[data-cond-value="0"]').inputValue(), 'you');
      assert.ok((await dialog.innerText()).includes('approvals and questions for you'));
      await dialog.getByRole('button', {name: 'Save'}).click();
      await rail.locator('#bot-sidebar-done').click();
      await rail.locator('#bot-sidebar-edit:not([hidden])').waitFor();
      await page.waitForFunction(() => document.querySelector('#t-blocked .ttl')?.textContent === 'Wait for QA');
      // Outside edit mode: Files is not drawn, the rest in the new order, and Updates' height is its most.
      assert.equal(await widget('files').isHidden(), true);
      assert.deepEqual(await order(), ['tasks', 'files', 'updates']);
      assert.equal(await widget('updates').locator('.rail-sec').evaluate(el => getComputedStyle(el).maxHeight), updatesHeight + 'px');
      await page.evaluate(() => document.fonts.ready);
      await page.screenshot({path: `/tmp/bot-sidebar-${scheme}-${viewport.width}.png`});

      // Next visit: drawn from this browser's copy at once, while the server's answer is still on its way.
      server.hold = new Promise(resolve => { release = resolve; });
      await goto('#/bot/lead');
      await rail.locator('#bot-tab-t-c0').waitFor({state: 'attached'});
      assert.equal(await rail.evaluate(el => el.hasAttribute('data-loading')), false);
      assert.deepEqual(await order(), ['tasks', 'files', 'updates']);
      assert.equal(await widget('files').isHidden(), true);
      release();
      // QA: Ana may not change its sidebar, so no pencil.
      await goto('#/bot/qa');
      await page.waitForFunction(() => !document.querySelector('#pane-tasks')?.hasAttribute('data-loading') && document.querySelector('#bot-tab-t-c3'));
      assert.equal(await rail.locator('#bot-sidebar-edit').isHidden(), true);
      assert.deepEqual(errors, [], at);
      // The edit view, for the record.
      await goto('#/bot/lead');
      await rail.locator('#bot-sidebar-edit:not([hidden])').click();
      await page.evaluate(() => document.fonts.ready);
      await page.screenshot({path: `/tmp/bot-sidebar-${scheme}-${viewport.width}-edit.png`});
      await context.close();
    }
    // A read-only viewer gets the same default filters: one wait is Blocked 1 and Doing 0.
    const isolated = {layouts: {}, puts: [], page: 200, tasks: [task('one-wait', 'Wait for the builder', {owner: 'bot:qa', status: 'waiting', waiting_on: null, relations: {blocks: [{id: 'builder', owner: 'bot:lead', title: 'Build it', status: 'doing', direction: 'in'}]}})]};
    const readOnly = await open(browser, {width:1280,height:900}, 'light', isolated);
    await readOnly.page.goto('https://tico-ui.test/#/bot/qa/tasks');
    await readOnly.page.waitForFunction(() => document.querySelector('#cnt-blocked')?.textContent === '1');
    assert.equal(await readOnly.page.locator('#cnt-active').innerText(), '0');
    assert.equal(await readOnly.page.locator('#bot-tasks-board').count(), 0);
    assert.equal(await readOnly.page.locator('#bot-sidebar-edit').isHidden(), true);
    await readOnly.page.locator('#bot-tab-t-blocked').click();
    assert.deepEqual(await readOnly.page.locator('#t-blocked .ttl').allInnerTexts(), ['Wait for the builder']);
    await readOnly.page.locator('#bot-tab-t-active').click();
    assert.equal(await readOnly.page.locator('#t-open .trow').count(), 0);
    assert.deepEqual(readOnly.errors, []);
    await readOnly.context.close();
    const filtered = await open(browser, {width:1280,height:900}, 'light', {puts: [], page: 200, layouts: {lead: {
      tabs: [{name: 'Bot waits', conditions: [{field: 'waiting_on', value: 'bot'}]},
             {name: 'People waits', conditions: [{field: 'waiting_on', value: 'person'}]}], widgets: WIDGETS}}});
    await filtered.page.goto('https://tico-ui.test/#/bot/lead/tasks');
    await filtered.page.waitForFunction(() => document.querySelector('#cnt-c0')?.textContent === '1');
    await filtered.page.locator('#bot-tab-t-c0').click();
    assert.deepEqual(await filtered.page.locator('#t-c0 .ttl').allInnerTexts(), ['Wait for QA']);
    assert.match(await filtered.page.locator('#t-c0 .trow-waits').innerText(), /Waiting on QA Engineer: Waiting for the test machine/);
    await filtered.page.locator('#bot-tab-t-c1').click();
    assert.deepEqual(await filtered.page.locator('#t-c1 .ttl').allInnerTexts(), ['Turn on the screen default']);
    assert.deepEqual(filtered.errors, []);
    await filtered.context.close();
    // A sidebar of pages: the built-ins hidden, a page shown with its title; Add puts another page in the draft, its
    // cross takes one out, and Done saves them as widgets.
    const pages = {layouts: {lead: {tabs: DEFAULT, widgets: [{id: 'page:f1', hidden: false, height: null},
      ...WIDGETS.map(w => ({...w, hidden: true}))]}}, puts: [], page: 200};
    const paged = await open(browser, {width: 1280, height: 900}, 'light', pages);
    const prail = paged.page.locator('#pane-tasks');
    const shown = () => prail.evaluate(el => [...el.querySelectorAll(':scope>.bot-w')].filter(w => w.offsetParent).map(w => w.dataset.widget));
    await paged.page.goto('https://tico-ui.test/#/bot/lead');
    await paged.page.waitForFunction(() => document.querySelector('#pane-tasks .bot-w[data-widget="page:f1"]')?.textContent.includes('Weekly numbers'));
    assert.deepEqual(await shown(), ['page:f1'], 'a sidebar of pages only');
    const pencil = await prail.locator('#bot-sidebar-edit').boundingBox(), first = await prail.locator('.bot-w[data-widget="page:f1"] .bot-pin > :first-child').boundingBox();
    assert.ok(pencil.y + pencil.height <= first.y + 1, 'the edit icon sits above the page, not on it');
    if (process.env.TICO_UI_SHOTS) await paged.page.screenshot({path: require('node:path').join(process.env.TICO_UI_SHOTS, 'bot-sidebar-pages.png')});
    await prail.locator('#bot-sidebar-edit').click();
    await prail.locator('#bot-sidebar-add').click();
    await paged.page.locator('#bot-pin-dialog[open] [name="page"][value="f2"]').check();
    await paged.page.locator('#bot-pin-dialog[open] [type="submit"]').click();
    await paged.page.waitForFunction(() => document.querySelector('#pane-tasks .bot-w[data-widget="page:f2"]')?.textContent.includes('Release plan'));
    await prail.locator('.bot-w[data-widget="page:f1"] [data-w-remove]').click();
    assert.equal(await prail.locator('.bot-w[data-widget="page:f1"]').count(), 0);
    await prail.locator('#bot-sidebar-done').click();
    await paged.page.waitForFunction(() => !document.querySelector('#pane-tasks.editing'));
    assert.deepEqual(pages.puts.at(-1).body.widgets.map(w => w.id + (w.hidden ? ' hidden' : '')), ['tasks hidden', 'updates hidden', 'files hidden', 'page:f2']);
    assert.deepEqual(await shown(), ['page:f2']);
    assert.deepEqual(paged.errors, []);
    await paged.context.close();
    console.log('bot-task-tabs: ok');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
