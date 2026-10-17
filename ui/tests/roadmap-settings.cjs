// Offline browser regression: Settings > Tasks > Public roadmap, Suggestions go to. The screen says what the server's
// suggestions_receiver answered (Automatic's bot, or a chosen bot that is archived or paused), never a blank; saving the
// other settings leaves an archived choice alone; members see it read-only; Health's link (#/settings/roadmap) brings
// the card into view, also when the card was drawn on an earlier visit.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
// /employees leaves archived bots out, as the server does; the archived bot's name comes from GET /v2/bots/{slug}.
const bots = [
  {name: 'product-lead', display_name: 'Head of Product', status: 'active', users: [], bot_owners: []},
  {name: 'helper', display_name: 'Helper', status: 'active', users: [], bot_owners: []},
  {name: 'librarian', display_name: 'Librarian', status: 'active', users: [], bot_owners: []},
  {name: 'scout', display_name: 'Scout', status: 'paused', users: [], bot_owners: []},
];
const base = {labels: ['roadmap'], cors_origins: ['https://acme.example'], action_limit: 10, suggestion_limit: 20, bot_public_approval: true};
// Helper is not what a template rule would pick: the screen must show the server's answer, not work one out.
const automatic = {...base, suggestions_bot: null, suggestions_receiver: {bot: 'helper', name: 'Helper', source: 'automatic'}};
const nobody = {...base, suggestions_bot: null, suggestions_receiver: {bot: null, name: null, source: 'automatic'}};
const archived = {...base, suggestions_bot: 'product-manager', suggestions_receiver: {bot: 'helper', name: 'Helper', source: 'chosen_archived'}};
const paused = {...base, suggestions_bot: 'scout', suggestions_receiver: {bot: null, name: null, source: 'chosen_inactive'}};
const types = Array.from({length: 12}, (_, i) => ({id: 'type-' + i, name: 'Type ' + i, steps: [], roles: []}));
(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  const errors = [], writes = [];
  async function open(me, settings, height = 900) {
    const page = await browser.newPage({viewport: {width: 1440, height}, serviceWorkers: 'block'});
    page.on('pageerror', e => errors.push(e.message));
    await page.route('**/*', route => {
      const req = route.request(), p = new URL(req.url()).pathname;
      const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
      if (ui && fs.existsSync(uiFile(ui[1])))
        return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
      if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      const config = {version: '0.1.0', app_name: 'Tico', local: true};
      if (p === '/api/me') return json({id: 'ana', name: 'Ana', email: 'ana@acme.example', cloud: true, registered: true, config, ...me});
      if (p === '/api/v2/config') return json(config);
      if (p === '/api/employees') return json(bots);
      if (p === '/api/v2/bots/product-manager') return json({slug: 'product-manager', display_name: 'Product Manager', state: 'archived'});
      if (p === '/api/v2/task-types') return json({types});
      if (p === '/api/issues') return json([]);
      if (p === '/api/humans' || p === '/api/people') return json({people: [{id: 'ana', name: 'Ana'}], teams: {}});
      if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: []});
      if (p === '/api/v2/status') return json({bots: []});
      if (p === '/api/v2/needs-you') return json({items: []});
      if (p === '/api/v2/tags') return json({tags: [{key: 'roadmap', label: 'roadmap', is_template: false}]});
      if (p === '/api/v2/roadmaps/settings') {
        if (req.method() === 'POST') {
          const body = req.postDataJSON(); writes.push(body);
          const chosen = body.suggestions_bot, receiver = !('suggestions_bot' in body) ? settings.suggestions_receiver
            : chosen ? {bot: chosen, name: bots.find(b => b.name === chosen).display_name, source: 'chosen'} : automatic.suggestions_receiver;
          settings = {...settings, ...body, suggestions_receiver: receiver};
        }
        return json(settings);
      }
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      return json({});
    });
    // Health's fix link: it lands on Settings > Tasks with the Public roadmap card in view.
    await page.goto('https://tico-ui.test/#/settings/roadmap');
    await page.locator('#settings-roadmaps select[name=suggestions_bot]').waitFor();
    return page;
  }
  const owner = {role: 'owner'};
  const picker = page => page.locator('#settings-roadmaps select[name=suggestions_bot]');
  const chosenText = page => picker(page).locator('option:checked').textContent();
  const save = async page => {
    await page.locator('#settings-roadmaps [type=submit]').click();
    await page.locator('#settings-roadmaps [data-status]', {hasText: 'Saved'}).waitFor();
  };
  // The card's top is inside #main's visible area once the frame after navigation has run.
  const inView = page => page.waitForFunction(() => {
    const main = document.querySelector('#main').getBoundingClientRect(), card = document.querySelector('#settings-roadmaps').getBoundingClientRect();
    return card.top >= main.top - 1 && card.top < main.bottom - 40;
  }, null, {timeout: 5000}).then(() => true, () => false);
  try {
    // Automatic: the field names the bot the server resolved, and lists every active bot, built-ins included.
    let page = await open(owner, automatic);
    assert.equal(await page.locator('[data-settings-tab=tasks]').getAttribute('aria-selected'), 'true', 'Health link opens Settings > Tasks');
    assert.equal(await picker(page).evaluate(el => document.activeElement === el), true, 'Health link puts focus on Suggestions go to');
    assert.equal(await chosenText(page), 'Automatic: Helper');
    assert.deepEqual(await picker(page).locator('option').evaluateAll(os => os.map(o => o.value)), ['', 'product-lead', 'helper', 'librarian'], 'Automatic, then active bots by name');
    // Choosing a bot sends it; the redraw shows the server's answer.
    await picker(page).selectOption('product-lead');
    await save(page);
    assert.equal(writes.at(-1).suggestions_bot, 'product-lead');
    assert.equal(await chosenText(page), 'Head of Product');
    await page.close();

    // Health's link on a short screen, first fresh and then after the card was drawn on an earlier visit.
    page = await open(owner, automatic, 420);
    assert.equal(await inView(page), true, 'fresh load: Public roadmap is in view');
    await page.locator('[data-settings-tab=devices]').click();
    await page.evaluate(() => { location.hash = '#/settings/roadmap'; });
    await page.waitForFunction(() => location.hash === '#/settings' && !document.querySelector('#settings-tasks').hidden);
    assert.equal(await inView(page), true, 'card drawn earlier: Public roadmap is in view after the router resets the scroll');
    await page.close();

    // No bot qualifies: Automatic says so.
    page = await open(owner, nobody);
    assert.equal(await chosenText(page), 'Automatic: no bot can take suggestions');
    await page.close();

    // A chosen bot that was archived shows as itself, and saving the rest doesn't resend it (the server refuses it).
    page = await open(owner, archived);
    assert.equal(await chosenText(page), 'Product Manager (archived)');
    assert.match(await page.locator('#settings-roadmaps [data-receiver]').textContent(), /is archived; suggestions now go to Helper/);
    await page.locator('#settings-roadmaps textarea[name=cors_origins]').fill('https://acme.example\nhttps://www.acme.example');
    await save(page);
    assert.equal('suggestions_bot' in writes.at(-1), false, 'an unchanged archived choice is not sent');
    await picker(page).selectOption('');
    await save(page);
    assert.equal(writes.at(-1).suggestions_bot, null, 'Automatic is sent as null');
    await page.close();

    // A chosen bot that is paused shows as paused, with where suggestions go now.
    page = await open(owner, paused);
    assert.equal(await chosenText(page), 'Scout (paused)');
    assert.match(await page.locator('#settings-roadmaps [data-receiver]').textContent(), /isn't active; no bot can take suggestions/);
    await page.close();

    // An admin who isn't the owner edits it.
    page = await open({role: 'human', bot_admin: true}, automatic);
    assert.equal(await picker(page).isDisabled(), false);
    await picker(page).selectOption('librarian');
    await save(page);
    assert.equal(writes.at(-1).suggestions_bot, 'librarian');
    await page.close();

    // A member sees the setting and what it means, read-only, with nothing to save.
    const before = writes.length;
    page = await open({role: 'human'}, automatic);
    assert.equal(await chosenText(page), 'Automatic: Helper');
    assert.equal(await picker(page).isDisabled(), true);
    assert.equal(await page.locator('#settings-roadmaps [type=submit]').count(), 0);
    assert.equal(writes.length, before);
    await page.close();
    assert.deepEqual(errors, []);
    console.log('roadmap settings ok');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
