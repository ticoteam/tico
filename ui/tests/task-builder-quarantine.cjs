// All reads are intercepted. Quarantine status changes independently of unchanged tickets and typed drafts.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    const context = await browser.newContext({viewport: {width: 1200, height: 900}, timezoneId: 'America/Los_Angeles', serviceWorkers: 'block'});
    const page = await context.newPage(), errors = [];
    page.setDefaultTimeout(10000);
    page.on('pageerror', error => errors.push(error.message));
    const type = {id: 'delivery', name: 'Delivery', role: 'engineering', steps: [
      {id: 'building', name: 'Building', status: 'doing', position: 0},
      {id: 'shipped', name: 'Shipped', status: 'done', position: 1}]};
    const now = '2030-10-26T12:00:00Z';
    const task = (id, owner, extra = {}) => ({id, title: id, body: 'Build the requested change.', owner,
      requester: 'human:ana', type_id: type.id, step_id: 'building', type: {id: type.id, name: type.name}, step: type.steps[0],
      status: 'doing', version: 1, lane: 'company', labels: [], links: [], parts: {total: 0, done: 0},
      rank: 1, created_at: now, updated_at: now, ...extra});
    const tasks = [task('Automatic cooldown', 'bot:builder-a'), task('Another ticket', 'bot:builder-a'),
      task('Manual quarantine', 'human:ana', {roles: {builder: ['bot:builder-b']}}), task('Healthy builder', 'bot:healthy'),
      task('General work', 'bot:builder-a', {type_id: 'general', type: {id: 'general', name: 'General'}}),
      task('Completed work', 'bot:builder-a', {status: 'done', step_id: 'shipped', step: type.steps[1]}),
      task('Declined work', 'bot:builder-a', {status: 'declined'}),
      task('Two builders', 'bot:builder-a', {roles: {builder: ['bot:builder-a', 'bot:builder-c']}})];
    const active = slug => ({bot: slug, state: 'idle', bot_state: 'active'});
    const held = (slug, notice, auto = true) => ({bot: slug, state: 'quarantined', bot_state: 'quarantined',
      quarantine: {auto, resumes_at: auto ? '2030-10-26T13:00:00Z' : null, builder_notice: notice,
        why: 'Manager-only private detail must never appear on a ticket.'}});
    let statuses = [active('builder-a'), active('builder-b'), active('healthy')];
    const auto = held('builder-a', 'Builder quarantined: resumes 13:00 UTC');
    const manual = held('builder-b', 'Builder quarantined: until a person clears it', false);
    await page.addInitScript(() => localStorage.setItem('tico.tasks.type', 'delivery'));
    await page.route('**/*', async route => {
      const url = new URL(route.request().url()), p = url.pathname;
      const json = data => route.fulfill({contentType: 'application/json', body: JSON.stringify(data)});
      if (url.origin !== 'https://tico-ui.test') return route.abort();
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
      if (ui && fs.existsSync(uiFile(ui[1]))) return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
      if (p === '/vendor/marked.min.js') return route.fulfill({contentType: 'application/javascript', body: fs.readFileSync(uiFile(p.slice(1)), 'utf8')});
      if (p === '/api/me') return json({id: 'ana', name: 'Ana', email: 'ana@acme.example', role: 'human', mover: false, cloud: true});
      if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana'}]});
      if (p === '/api/employees') return json(['builder-a', 'builder-b', 'builder-c', 'healthy'].map(name => ({name, display_name: name, host: 'keeper', status: 'active', can_chat: true})));
      if (p === '/api/status') return json({active: [], employees: []});
      if (p === '/api/v2/status') return json({bots: statuses});
      if (p === '/api/v2/task-types') return json({types: [type, {id: 'general', name: 'General', steps: []}]});
      if (p === '/api/v2/tasks') {
        const wanted = url.searchParams.get('status')?.split(',');
        return json({tasks: wanted ? tasks.filter(t => wanted.includes(t.status)) : tasks, next_offset: null});
      }
      if (p === '/api/v2/tasks/labels') return json({labels: [], tags: []});
      if (p.startsWith('/api/v2/preferences/')) return json({value: {type: 'delivery', view: 'board', views: 2}});
      const one = p.match(/^\/api\/v2\/tasks\/([^/]+)$/);
      if (one) return json({task: tasks.find(t => t.id === decodeURIComponent(one[1])), children: [], comments: [], events: []});
      if (p.endsWith('/tree')) return json([]);
      return json(unrouted(p));
    });
    await page.goto('https://tico-ui.test/#/tasks');
    await page.locator('.bcard[data-task-key="tAutomatic cooldown"]').waitFor();
    assert.equal(await page.locator('.task-builder-notice').count(), 0);
    // A bot-status event alone changes all its cards and one column notice, with no ticket version change.
    statuses = [auto, manual, held('builder-c', auto.quarantine.builder_notice), active('healthy')];
    await page.evaluate(rows => rows.forEach(status => liveEmit('bots', {bot: status.bot, status})), statuses);
    await page.locator('.bcard[data-task-key="tAutomatic cooldown"] .task-builder-notice').waitFor();
    for (const id of ['Automatic cooldown', 'Another ticket']) assert.equal(await page.locator(`.bcard[data-task-key="t${id}"] .task-builder-notice`).innerText(), auto.quarantine.builder_notice);
    assert.equal(await page.locator('.bcard[data-task-key="tManual quarantine"] .task-builder-notice').innerText(), manual.quarantine.builder_notice);
    assert.equal(await page.locator('.bcard[data-task-key="tHealthy builder"] .task-builder-notice').count(), 0);
    assert.equal(await page.locator('.bcol > .task-builder-notices .task-builder-notice').count(), 3, 'column deduplicates each builder');
    assert.doesNotMatch(await page.locator('.board').innerText(), /Manager-only private detail/);
    assert.equal(await page.evaluate(tasks => taskBuilderActors(tasks.find(t => t.title === 'General work')).length, tasks), 0);
    assert.equal(await page.evaluate(tasks => taskBuilderActors(tasks.find(t => t.title === 'Completed work')).length, tasks), 0);
    const both = page.locator('.bcard[data-task-key="tTwo builders"] .task-builder-notice');
    assert.equal(await both.count(), 2, 'owner repeated in builder role is deduplicated');
    assert.deepEqual(await both.locator('strong').allTextContents(), ['builder-a', 'builder-c']);
    for (const text of await both.allTextContents()) assert(text.endsWith(auto.quarantine.builder_notice));
    if (process.env.DEV595_SHOTS) await page.screenshot({path: path.join(process.env.DEV595_SHOTS, 'board.png'), fullPage: true});
    // Lifting one builder leaves the other visible in its column. This includes already-open cards.
    statuses[0] = active('builder-a');
    await page.evaluate(status => liveEmit('bots', {bot: status.bot, status}), statuses[0]);
    await page.waitForFunction(() => !document.querySelector('.bcard[data-task-key="tAutomatic cooldown"] .task-builder-notice'));
    assert.equal(await page.locator('.bcol > .task-builder-notices .task-builder-notice').count(), 2);
    statuses[0] = auto;
    await page.evaluate(status => liveEmit('bots', {bot: status.bot, status}), auto);
    await page.goto('https://tico-ui.test/#/task/Automatic%20cooldown');
    const modal = page.locator('#task-modal');
    await modal.locator('.task-builder-notice').waitFor();
    assert.equal(await modal.locator('.task-builder-notice').innerText(), auto.quarantine.builder_notice);
    const draft = modal.locator('textarea[aria-label="Message"]');
    await draft.fill('Keep this unsent draft');
    await page.evaluate(status => liveEmit('bots', {bot: status.bot, status}), active('builder-a'));
    await modal.locator('.task-builder-notice').waitFor({state: 'detached'});
    assert.equal(await draft.inputValue(), 'Keep this unsent draft');
    // The fallback snapshot refresh updates an open ticket too, and keeps explicit UTC dates on a non-UTC phone.
    const overnight = held('builder-a', 'Builder quarantined: resumes 2030-10-27 00:55 UTC');
    statuses[0] = overnight;
    await page.setViewportSize({width: 390, height: 844});
    await page.evaluate(() => refresh(true));
    assert.equal(await modal.locator('.task-builder-notice').innerText(), overnight.quarantine.builder_notice);
    assert.equal(await draft.inputValue(), 'Keep this unsent draft');
    const bounds = await modal.locator('.task-builder-notice').boundingBox();
    assert(bounds.width <= 390 && bounds.x >= 0 && bounds.x + bounds.width <= 391, 'phone notice fits');
    if (process.env.DEV595_SHOTS) await page.screenshot({path: path.join(process.env.DEV595_SHOTS, 'ticket-phone.png'), fullPage: true});
    // Manual quarantine on the ticket page, then its lift, use the same notice as cards.
    await page.goto('https://tico-ui.test/#/task/Manual%20quarantine');
    await page.waitForFunction(() => S.route === '#/task/Manual%20quarantine');   // a page change draws a frame later (its fade)
    await modal.locator('.task-builder-notice').waitFor();
    assert.equal(await modal.locator('.task-builder-notice').innerText(), manual.quarantine.builder_notice);
    await page.evaluate(status => liveEmit('bots', {bot: status.bot, status}), active('builder-b'));
    await modal.locator('.task-builder-notice').waitFor({state: 'detached'});
    // Identical deadlines still identify both builders on the ticket page.
    statuses[0] = auto;
    await page.evaluate(status => liveEmit('bots', {bot: status.bot, status}), auto);
    await page.goto('https://tico-ui.test/#/task/Two%20builders');
    await modal.locator('.task-builder-notice').first().waitFor();
    assert.deepEqual(await modal.locator('.task-builder-notice strong').allTextContents(), ['builder-a', 'builder-c']);
    // A declined task is readable directly but must not inherit its bot owner's quarantine notice.
    await page.goto('https://tico-ui.test/#/task/Declined%20work');
    await page.waitForFunction(() => document.querySelector('#task-modal .tmodal-title')?.textContent === 'Declined work');
    assert.equal(await modal.locator('.task-builder-notice').count(), 0);
    assert.deepEqual(errors, []);
    console.log('PASS: quarantine cards, column deduplication, ticket page, manual/auto/lift, healthy/general/finished/declined controls, named multiple builders, UTC phone and preserved drafts');
    await context.close();
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
