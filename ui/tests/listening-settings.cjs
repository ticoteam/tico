// Settings > Listening (ui/app/settings-listening.js): the owner and admins read every destination (a file one is
// read-only, marked "From listening.yaml"); only the owner adds, edits and removes the ones saved in Settings; the
// form sends the category, receiver, threshold, readers, optional skip rule and what; the server's refusal shows in the
// form; the tab is absent for a member; the Beta label stays; a phone has no sideways scroll. Fixtures only.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile, beta} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');

const CATEGORIES = ['market', 'content', 'lead', 'vendor_pitch'].map(id => ({id, label: id.replace('_', ' ')}));
const RECEIVERS = [{actor: 'bot:librarian', name: 'Librarian'}, {actor: 'bot:content', name: 'Content'}, {actor: 'bot:sales-ops', name: 'Sales Ops'}];

async function open(browser, role, width = 1440) {
  const page = await browser.newPage({viewport: {width, height: 900}, serviceWorkers: 'block'});
  const errors = [], writes = [];
  const dests = [
    {key: 'market', category: 'market', threshold: 0.7, receiver: 'bot:librarian', readers: [], unless: null, what: 'a post about the market', source: 'settings', overrides_yaml: false},
    {key: 'leads', category: 'lead', threshold: 0.75, receiver: 'bot:sales-ops', readers: ['bot:content'],
      unless: {category: 'vendor_pitch', threshold: 0.7}, what: 'a team lead', source: 'yaml', overrides_yaml: false}];
  page.on('pageerror', e => errors.push(e.message));
  await page.route('**/*', route => {
    const req = route.request(), p = new URL(req.url()).pathname;
    const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1])))
      return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
    if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const config = {version: '0.1.0', app_name: 'Tico', beta};
    if (p === '/api/me') return json({id: role === 'owner' ? 'ana' : 'cara', role: role === 'owner' ? 'owner' : 'human', bot_admin: role === 'admin',
      name: 'Ana', email: 'ana@example.com', cloud: true, registered: true, config});
    if (p === '/api/v2/config') return json(config);
    if (p === '/api/employees') return json([{name: 'librarian', display_name: 'Librarian', status: 'active'}]);
    if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: [], schedules: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/models') return json({models: [], harnesses: []});
    if (p === '/api/v2/access') return json({rules: {}});
    if (p === '/api/v2/listening/destinations' && req.method() === 'GET')
      return json({destinations: dests, categories: CATEGORIES, receivers: RECEIVERS});
    const one = p.match(/^\/api\/v2\/listening\/destinations\/([^/]+)(\/remove)?$/);
    if (one) {
      const body = req.postDataJSON() || {};
      writes.push({method: req.method(), key: one[1], remove: !!one[2], body});
      if (one[2]) { dests.splice(dests.findIndex(d => d.key === one[1]), 1); return json({removed: one[1]}); }
      if (body.threshold > 1) return json({error: {code: 'invalid', detail: 'threshold is a number above 0 and up to 1'}}, 422);
      const saved = {key: one[1], readers: [], unless: null, what: '', ...body, source: 'settings', overrides_yaml: false};
      const at = dests.findIndex(d => d.key === one[1]);
      if (at >= 0) dests[at] = saved; else dests.push(saved);
      return json({destination: saved});
    }
    if (p.endsWith('/watch') || p === '/api/v2/events') return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
  await page.goto('https://tico-ui.test/#/settings');
  await page.locator('#settings-tabs').waitFor();
  if (role !== 'member') {
    await page.locator('#settings-more summary').click();
    await page.locator('[data-settings-tab="listening"]').click();
    await page.locator('[data-listen-dest]').first().waitFor();
  }
  return {page, errors, writes, dests};
}
const row = (page, key) => page.locator(`[data-listen-dest="${key}"]`);

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    {
      const {page, errors, writes} = await open(browser, 'owner');
      assert.equal(await page.locator('#settings-listening h2').innerText(), 'Listening');
      assert.equal(await page.locator('#settings-listening .pill.beta').count(), 1, 'the Beta label stays');
      assert.match(await row(page, 'market').innerText(), /market at 0\.7 or above → Librarian/);
      assert.match(await row(page, 'leads').innerText(), /lead at 0\.75 or above → Sales Ops, not when vendor pitch is 0\.7 or above/);
      assert.match(await row(page, 'leads').innerText(), /Can read: Content/);
      assert.equal(await row(page, 'leads').locator('[data-listen-source]').innerText(), 'From listening.yaml');
      assert.equal(await row(page, 'leads').locator('button').count(), 0, 'a file destination is read-only');
      assert.equal(await row(page, 'market').locator('[data-listen-edit], [data-listen-remove]').count(), 2);
      // Add: the form sends exactly what was chosen.
      await page.locator('[data-listen-add]').click();
      await page.locator('[name=key]').fill('Content');
      await page.locator('[name=category]').selectOption('content');
      await page.locator('[name=receiver]').selectOption('bot:content');
      await page.locator('[name=threshold]').fill('0.8');
      await page.locator('[name=readers]').selectOption(['bot:librarian']);
      await page.locator('[name=unless_category]').selectOption('vendor_pitch');
      await page.locator('[name=what]').fill('a question an article could answer');
      await page.locator('[data-listen-form] button[type=submit]').click();
      await row(page, 'content').waitFor();
      assert.deepEqual(writes, [{method: 'PUT', key: 'content', remove: false, body: {category: 'content', receiver: 'bot:content', threshold: 0.8,
        readers: ['bot:librarian'], what: 'a question an article could answer', unless: {category: 'vendor_pitch', threshold: 0.7}}}]);
      await page.locator('[data-listen-form]').waitFor({state: 'detached'});     // the form closes after saving
      // Edit: the name is fixed; the server's refusal stays in the form.
      await row(page, 'market').locator('[data-listen-edit]').click();
      assert.equal(await page.locator('[name=key]').getAttribute('readonly'), '');
      assert.equal(await page.locator('[name=threshold]').inputValue(), '0.7');
      await page.locator('[name=threshold]').fill('1.5');
      await page.locator('[data-listen-form] button[type=submit]').click();
      await page.locator('[data-listen-status]').filter({hasText: /above 0/}).waitFor();
      assert.equal(await page.locator('[data-listen-form]').count(), 1, 'a refused save keeps the form');
      await page.locator('[data-listen-cancel]').click();
      await page.locator('[data-listen-form]').waitFor({state: 'detached'});
      // Remove asks first, then goes.
      const ask = page.locator('dialog.settings-confirm');
      await row(page, 'content').locator('[data-listen-remove]').click();
      await ask.locator('[data-confirm-cancel]').click();
      await ask.waitFor({state: 'detached'});
      assert.equal(await row(page, 'content').count(), 1);
      await row(page, 'content').locator('[data-listen-remove]').click();
      await ask.locator('[data-confirm-ok]').click();
      await row(page, 'content').waitFor({state: 'detached'});
      assert.equal(writes.at(-1).remove, true);
      // A phone: nothing spills sideways, in light and dark.
      for (const dark of [false, true]) {
        await page.evaluate(d => document.documentElement.dataset.theme = d ? 'dark' : 'light', dark);
        await page.setViewportSize({width: 390, height: 844});
        await page.locator('[data-listen-add]').click();
        await page.locator('[data-listen-form]').waitFor();
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), 'no sideways scroll at 390px');
        await page.locator('[data-listen-cancel]').click();
        await page.locator('[data-listen-form]').waitFor({state: 'detached'});
      }
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // An admin reads every destination and has no controls.
      const {page, errors} = await open(browser, 'admin');
      assert.equal(await page.locator('[data-listen-dest]').count(), 2);
      assert.equal(await page.locator('#set-listening button, #set-listening input, #set-listening select').count(), 0);
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // A member has no Listening tab.
      const {page} = await open(browser, 'member');
      await page.locator('#settings-more summary').click();
      assert.equal(await page.locator('[data-settings-tab="listening"]').count(), 0);
      await page.close();
    }
    console.log('listening-settings: ok');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
