// Settings > System > Screening (ui/app/settings-release.js, backend/screening.py): an owner or admin sees a switch per
// detector (Patterns, Model) and one row per door (Webhooks, Mail, Ideas, Watchers) with a mode, mail offering Off and
// Warn only; a change is one PUT; Run test
// shows passed/total and each sample; Recent lists holds and warnings with a Task link. A member never sees the card.
// Fits a phone with no sideways scroll. Fixtures only. TICO_SHOTS=<dir> also saves desktop and phone screenshots.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile, harnesses} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');

const SOURCES = [{id: 'webhook', name: 'Webhooks'}, {id: 'mail', name: 'Mail'}, {id: 'idea', name: 'Ideas'}, {id: 'watcher', name: 'Watchers'}];
const before = new Date(Date.now() - 20 * 60000).toISOString();
const view = () => ({modes: {webhook: 'hold', mail: 'warn', idea: 'hold', watcher: 'hold'}, layers: {patterns: true, model: true},
  sources: SOURCES, model: true,
  recent: [{action: 'held', source: 'webhook', score: 0.97, kind: 'ignore_previous_instructions', at: before, task: 't-held'},
    {action: 'warned', source: 'mail', score: null, kind: 'reveal_system_prompt', at: before, task: null}]});
const TEST = {passed: 7, total: 8, model: true, results: [
  {name: 'Ignore instructions', attack: true, action: 'hold', score: 0.98, ok: true},
  {name: 'Code review', attack: false, action: 'warn', score: 0.6, ok: false},
  {name: 'Refund request', attack: false, action: 'pass', score: 0.02, ok: true}]};

async function open(browser, role, viewport) {
  const page = await browser.newPage({viewport, serviceWorkers: 'block'});
  const errors = [], writes = [], data = view();
  page.on('pageerror', e => errors.push(e.message));
  await page.route('**/*', route => {
    const req = route.request(), p = new URL(req.url()).pathname;
    const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1])))
      return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
    if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const config = {version: '0.1.0', app_name: 'Tico'};
    if (p === '/api/me') return json({id: role === 'owner' ? 'ana' : 'cara', role: role === 'owner' ? 'owner' : 'human', bot_admin: role === 'admin',
      name: 'Ana', email: 'ana@example.com', cloud: true, registered: true, config});
    if (p === '/api/v2/config') return json(config);
    if (p === '/api/employees') return json([{name: 'ops', display_name: 'Ops', status: 'active'}]);
    if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/models') return json({models: [], harnesses});
    if (p === '/api/v2/access') return json({rules: {}});
    if (p === '/api/v2/system-routines') return json({now: new Date().toISOString(), departments: [], jobs: []});
    if (p === '/api/v2/screening') {
      if (role === 'member') return json({error: {code: 'forbidden', detail: 'Owners and admins only'}}, 403);
      if (req.method() === 'PUT') {
        const body = req.postDataJSON(); writes.push(body);
        for (const [k, v] of Object.entries(body)) (typeof v === 'boolean' ? data.layers : data.modes)[k] = v;
      }
      return json(data);
    }
    if (p === '/api/v2/screening/test') { writes.push('test'); return json(TEST); }
    if (p.endsWith('/watch') || p === '/api/v2/events') return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
  await page.goto('https://tico-ui.test/#/settings');
  await page.locator('#settings-tabs').waitFor();
  const tab = page.locator('[data-settings-tab="system"]');
  if (!await tab.isVisible()) await page.locator('#settings-more summary').click();
  await tab.click();
  return {page, errors, writes};
}

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  const shots = process.env.TICO_SHOTS;
  try {
    for (const [role, viewport] of [['owner', {width: 1440, height: 900}], ['admin', {width: 390, height: 844}]]) {
      const {page, errors, writes} = await open(browser, role, viewport);
      const card = page.locator('#set-screening-card');
      await card.locator('[data-screening-mode="webhook"]').waitFor();
      assert.equal((await card.locator('h2').innerText()).trim(), 'Screening');
      const names = await card.locator('.sb-row > span:first-child').allInnerTexts();
      assert.deepEqual(names.map(n => n.trim()), ['Patterns', 'Model', 'Webhooks', 'Mail', 'Ideas', 'Watchers', 'Test']);
      const patterns = card.locator('[data-screening-layer="patterns"]');
      assert.equal(await patterns.isChecked(), true);
      assert.equal(await card.locator('[data-screening-layer="model"]').isChecked(), true);
      const options = id => card.locator(`[data-screening-mode="${id}"] option`).evaluateAll(els => els.map(el => el.value));
      assert.deepEqual(await options('mail'), ['off', 'warn'], 'mail is only marked, never held');
      assert.deepEqual(await options('idea'), ['off', 'warn', 'hold']);
      assert.equal(await card.locator('[data-screening-mode="webhook"]').inputValue(), 'hold');
      // Recent: a hold links its task; a warning with no task shows a dash.
      const recent = await card.locator('.tm-row').allInnerTexts();
      assert.match(recent[0], /Held · Webhooks[\s\S]*97%[\s\S]*20m ago[\s\S]*Task/);
      assert.match(recent[1], /Warned · Mail[\s\S]*—/);
      assert.equal(await card.locator('.tm-row a[href="#/task/t-held"]').count(), 1);
      // A change is one PUT, and the select reads it back.
      await card.locator('[data-screening-mode="watcher"]').selectOption('warn');
      await page.waitForFunction(() => document.querySelector('#set-screening [data-screening-status]')?.textContent === 'Saved');
      assert.equal(await card.locator('[data-screening-mode="watcher"]').inputValue(), 'warn');
      // A detector switch is one PUT of its boolean, and the row reads Off.
      await patterns.click();
      await page.waitForFunction(() => /Off/.test(document.querySelector('#set-screening .sb-row')?.textContent || ''));
      assert.equal(await card.locator('[data-screening-layer="patterns"]').isChecked(), false);
      // Run test: the score, then each sample; a wrong one in red.
      await card.locator('[data-screening-test]').click();
      await card.locator('[data-screening-results] .tm-head').waitFor();
      assert.equal((await card.locator('[data-screening-results] .tm-head strong').innerText()).trim(), '7/8');
      const wrong = card.locator('[data-screening-results] .tm-row').nth(1);
      assert.match(await wrong.innerText(), /Code review[\s\S]*Safe[\s\S]*Warn 60%[\s\S]*Wrong/);
      assert.equal(await wrong.locator('.err').count(), 1);
      assert.deepEqual(writes, [{watcher: 'warn'}, {patterns: false}, 'test']);
      // Phone: nothing wider than the screen.
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), role + ': no sideways scroll');
      if (shots) {
        fs.mkdirSync(shots, {recursive: true});
        await card.scrollIntoViewIfNeeded();
        await card.screenshot({path: path.join(shots, `screening-${viewport.width}.png`)});
      }
      assert.deepEqual(errors, []);
      await page.close();
    }
    // A member: GET answers 403 and the card is never shown.
    const {page, errors} = await open(browser, 'member', {width: 1440, height: 900});
    await page.waitForTimeout(300);
    assert.equal(await page.locator('#set-screening-card:visible').count(), 0);
    assert.deepEqual(errors, []);
    await page.close();
    console.log('settings-screening: ok');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
