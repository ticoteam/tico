// Overview fallback line: bots whose plan ran out show above the campus as "N bots running on fallback since HH:MM,
// X limited", from the Health answer the app already polls; with nothing on fallback or limited the strip stays hidden.
// The 3D renderer is replaced by a stand-in. Fixtures only.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');

const SCENE = `export function createBuilding() {
  document.body.dataset.sceneReady = 'true'; document.querySelector('#loading').hidden = true;
  return {update() {}, link() { return true; }, clearLinks() {}, dispose() {}};
}`;

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1280, height: 860}, serviceWorkers: 'block'});
    const errors = [], since = '2026-10-13T10:04:00Z';
    const health = {audience: 'owner', checks: [], attention: 0, checked: since, stuck: [],
      on_fallback: [{bot: 'writer', harness: 'codex', changed_at: '2026-10-13T10:09:00Z'}, {bot: 'editor', harness: 'codex', changed_at: since}],
      limited: [{bot: 'vault', changed_at: since}]};
    page.on('pageerror', e => errors.push(e.message));
    await page.route('**/*', async route => {
      const url = new URL(route.request().url()), p = url.pathname;
      if (url.origin !== 'http://tico-ui.test') return route.abort();
      const json = body => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      if (p === '/tico/ui/overview/building.js') return route.fulfill({contentType: 'application/javascript', body: SCENE});
      if (p.startsWith('/tico/ui/')) {
        const name = p.slice('/tico/ui/'.length), file = uiFile(name);
        if (fs.existsSync(file)) return route.fulfill({body: fs.readFileSync(file), contentType: name.endsWith('.html') ? 'text/html'
          : name.endsWith('.css') ? 'text/css' : name.endsWith('.js') ? 'application/javascript' : 'application/octet-stream'});
      }
      if (p === '/api/employees') return json([]);
      if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana'}], org_groups: []});
      if (p === '/api/me') return json({id: 'ana', name: 'Ana', role: 'owner', cloud: true, config: {company_name: 'Acme Studio'}});
      if (p === '/api/v2/health') return json(health);
      return json(unrouted(p));
    });
    await page.goto('http://tico-ui.test/');
    await page.waitForFunction(() => location.hash === '#/updates');
    await page.evaluate(() => location.hash = '#/overview/campus');
    await page.frameLocator('#overview-frame').locator('body[data-scene-ready]').waitFor();
    await page.evaluate(() => hlRefresh());
    const strip = page.locator('#overview-stuck');
    await strip.locator('.overview-fallback').waitFor();
    const clock = await page.evaluate(at => new Date(at).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'}), since);
    assert.equal(await strip.locator('.overview-fallback').innerText(), `2 bots running on fallback since ${clock}, 1 limited`,
      'the earliest fallback start and the limited count');
    assert.equal(await strip.isHidden(), false);
    assert.equal(await strip.locator('strong').count(), 0, 'no Stuck heading when nothing is stuck');

    // Only limited bots: no fallback clause.
    health.on_fallback = [];
    await page.evaluate(() => hlRefresh());
    assert.equal(await strip.locator('.overview-fallback').innerText(), '1 limited');

    // Nothing on fallback and nothing limited: the strip hides again.
    health.limited = [];
    await page.evaluate(() => hlRefresh());
    assert.equal(await strip.isHidden(), true);
    assert.deepEqual(errors, []);
    console.log('overview-fallback: ok');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
