// Offline browser regression: Settings > Tasks > Public roadmap, Suggestions go to. The screen says what the server's
// suggestions_receiver answered (Automatic's bot, or a chosen bot that is archived), never a blank; saving the other
// settings leaves an archived choice alone; members see it read-only; Health's link (#/settings/roadmap) opens it.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const bots = [
  {name: 'product-lead', display_name: 'Head of Product', status: 'active', users: [], bot_owners: []},
  {name: 'helper', display_name: 'Helper', status: 'active', users: [], bot_owners: []},
  {name: 'product-manager', display_name: 'Product Manager', status: 'archived', users: [], bot_owners: []},
  {name: 'scout', display_name: 'Scout', status: 'paused', users: [], bot_owners: []},
];
const base = {labels: ['roadmap'], cors_origins: ['https://acme.example'], action_limit: 10, suggestion_limit: 20, bot_public_approval: true};
const automatic = {...base, suggestions_bot: null, suggestions_receiver: {bot: 'product-lead', name: 'Head of Product', source: 'automatic'}};
const archived = {...base, suggestions_bot: 'product-manager', suggestions_receiver: {bot: 'product-lead', name: 'Head of Product', source: 'chosen_archived'}};
(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  const errors = [], writes = [];
  async function open(role, settings) {
    const page = await browser.newPage({viewport: {width: 1440, height: 900}, serviceWorkers: 'block'});
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
      if (p === '/api/me') return json({id: 'ana', role, name: 'Ana', email: 'ana@acme.example', cloud: true, registered: true, config});
      if (p === '/api/v2/config') return json(config);
      if (p === '/api/employees') return json(bots);
      if (p === '/api/issues') return json([]);
      if (p === '/api/humans' || p === '/api/people') return json({people: [{id: 'ana', name: 'Ana'}], teams: {}});
      if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: []});
      if (p === '/api/v2/status') return json({bots: []});
      if (p === '/api/v2/needs-you') return json({items: []});
      if (p === '/api/v2/tags') return json({tags: [{key: 'roadmap', label: 'roadmap', is_template: false}]});
      if (p === '/api/v2/roadmaps/settings') {
        if (req.method() === 'POST') {
          const body = req.postDataJSON(); writes.push(body);
          const chosen = 'suggestions_bot' in body ? body.suggestions_bot : settings.suggestions_bot;
          settings = {...settings, ...body, suggestions_receiver: chosen ? {bot: chosen, name: bots.find(b => b.name === chosen).display_name, source: 'chosen'} : automatic.suggestions_receiver};
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
  try {
    // Automatic: the field names the bot the server resolved, and lists active bots only.
    let page = await open('owner', automatic);
    const picker = page.locator('#settings-roadmaps select[name=suggestions_bot]');
    assert.equal(await page.locator('[data-settings-tab=tasks]').getAttribute('aria-selected'), 'true', 'Health link opens Settings > Tasks');
    assert.equal(await picker.evaluate(el => document.activeElement === el), true, 'Health link puts focus on Suggestions go to');
    assert.equal(await picker.locator('option:checked').textContent(), 'Automatic: Head of Product');
    assert.deepEqual(await picker.locator('option').evaluateAll(os => os.map(o => o.value)), ['', 'product-lead', 'helper'], 'Automatic, then active bots by name');
    // Choosing a bot sends it; the redraw shows the server's answer.
    await picker.selectOption('helper');
    await page.locator('#settings-roadmaps [type=submit]').click();
    await page.locator('#settings-roadmaps [data-status]', {hasText: 'Saved'}).waitFor();
    assert.equal(writes.at(-1).suggestions_bot, 'helper');
    assert.equal(await picker.locator('option:checked').textContent(), 'Helper');
    await page.close();

    // A chosen bot that was archived shows as itself, and saving the rest doesn't resend it (the server refuses it).
    page = await open('owner', archived);
    const again = page.locator('#settings-roadmaps select[name=suggestions_bot]');
    assert.equal(await again.locator('option:checked').textContent(), 'Product Manager (archived)');
    assert.match(await page.locator('#settings-roadmaps [data-receiver]').textContent(), /is archived; suggestions now go to Head of Product/);
    await page.locator('#settings-roadmaps textarea[name=cors_origins]').fill('https://acme.example\nhttps://www.acme.example');
    await page.locator('#settings-roadmaps [type=submit]').click();
    await page.locator('#settings-roadmaps [data-status]', {hasText: 'Saved'}).waitFor();
    assert.equal('suggestions_bot' in writes.at(-1), false, 'an unchanged archived choice is not sent');
    await again.selectOption('');
    await page.locator('#settings-roadmaps [type=submit]').click();
    await page.locator('#settings-roadmaps [data-status]', {hasText: 'Saved'}).waitFor();
    assert.equal(writes.at(-1).suggestions_bot, null, 'Automatic is sent as null');
    await page.close();

    // A member sees the setting and what it means, read-only, with nothing to save.
    const before = writes.length;
    page = await open('human', automatic);
    const readOnly = page.locator('#settings-roadmaps select[name=suggestions_bot]');
    assert.equal(await readOnly.locator('option:checked').textContent(), 'Automatic: Head of Product');
    assert.equal(await readOnly.isDisabled(), true);
    assert.equal(await page.locator('#settings-roadmaps [type=submit]').count(), 0);
    assert.equal(writes.length, before);
    await page.close();
    assert.deepEqual(errors, []);
    console.log('roadmap settings ok');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
