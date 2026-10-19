// Real subscription component with isolated fixture data: rename never changes routing keys.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {harnesses} = require('./support/page.cjs');
(async () => {
  const browser = await chromium.launch({headless: true,
    channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    const page = await browser.newPage({viewport: {width: 390, height: 844}});
    const errors = []; page.on('pageerror', e => errors.push(e.message));
    await page.setContent('<main style="grid-column:1/-1;padding:16px"><section id="settings-subs" class="subs-card"><div id="set-subs"></div></section><div id="rows"><div class="sb-sub" data-bot-sub-line></div><div class="sb-row" data-bot-sub-row><span>Subscription</span><select class="settings-inline-select"></select><span></span></div></div></main>');
    for (const name of ['tokens', 'base', 'components', 'settings']) {
      await page.addStyleTag({content: fs.readFileSync(path.join(__dirname, `../styles/${name}.css`), 'utf8')});
    }
    await page.addScriptTag({content: fs.readFileSync(path.join(__dirname, '../app/settings.js'), 'utf8')});
    await page.evaluate(harnesses => {
      window.$ = s => document.querySelector(s);
      window.esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
      window.formBusy = el => [...el.querySelectorAll('input')].some(f => f === document.activeElement || f.value !== f.defaultValue); window.toast = () => {};
      // ui/app/settings-devices.js, loaded before subscriptions.js in ui/index.html; subscriptions.cjs covers the shared-login line.
      window.machineSharedLoginHTML = () => '';
      window.S = {me: {id: 'ana', role: 'owner', cloud: true}, orgGroups: [], emps: [{name: 'builder', machine: {runner_id: 'r1', operator: 'ana'}}]};
      SETTINGS_DATA = {machines: [{id: 'r1', label: 'North', operator: 'ana'}, {id: 'r2', label: 'South', operator: 'ana'}]};
      window.fixture = {profiles_by_computer: ['r1','r2'].map((runner_id, i) => ({runner_id, label: ['North', 'South'][i], profiles: [
        {name: 'engineering', id: 'subscription:' + JSON.stringify([runner_id, 'engineering']), display_name: 'Engineering', runtimes: {codex: {signed_in: true}}}]})),
        assignments: [{scope: 'bot', target: 'builder', profile: 'engineering'}]};
      window.writes = [];
      window.post = async (url, body) => { writes.push({url, body}); };
      window.get = async url => url === '/v2/models' ? {harnesses} : structuredClone(fixture);
      window.put = async (url, body) => {
        writes.push({url, body});
        if (url === '/v2/subscriptions/name') {
          const p = fixture.profiles_by_computer.find(c => c.runner_id === body.runner_id).profiles.find(p => p.name === body.profile);
          p.display_name = body.display_name.trim(); return {id:p.id, display_name:p.display_name};
        }
        if (url === '/v2/subscriptions/weekly') {
          const p = fixture.profiles_by_computer.find(c => c.runner_id === body.runner_id).profiles.find(p => p.name === body.profile);
          p.runtimes[body.runtime].weekly = {used_percent: body.used_percent, resets_at: body.resets_at, reported_at: new Date().toISOString(), source: 'manual'};
          return {};
        }
      };
    }, harnesses);
    await page.addScriptTag({content: fs.readFileSync(path.join(__dirname, '../app/subscriptions.js'), 'utf8')});
    await page.evaluate(() => renderSettingsSubs(true));
    const north = page.locator('.subs-pc').first();
    assert.equal(await north.locator('details').count(), 1, 'one disclosure for secondary controls');
    assert.equal(await north.locator('[data-subs-rename]').isVisible(), false);
    assert.equal(await north.locator('[data-subs-weekly]').isVisible(), false);
    assert.equal(await north.locator('[data-subs-refresh]').isVisible(), true);
    assert.match(await north.locator('.subs-weekly').innerText(), /usage unknown/);
    // Keep keyboard refresh and its routing contract in this retained subscription path.
    await page.evaluate(() => {
      const render = renderSettingsSubs;
      renderSettingsSubs = async (...args) => { await render(...args); window.refreshRendered = true; };
    });
    await north.locator('[data-subs-refresh]').focus();
    await page.keyboard.press('Enter');
    await page.waitForFunction(() => window.refreshRendered);
    assert.deepEqual(await page.evaluate(() => writes.shift()), {url: '/v2/subscriptions/refresh', body: {
      runner_id: 'r1', profile: 'engineering', runtime: 'codex'}});
    await north.getByText('Manage subscription', {exact: true}).focus();
    await page.keyboard.press('Enter');
    await page.evaluate(() => renderSettingsSubs());
    assert.equal(await north.locator('details').first().getAttribute('open'), '', 'polling preserves the open controls');
    await north.locator('[name=display_name]').fill('Research <img src=x onerror=alert(1)>');
    await page.evaluate(() => renderSettingsSubs());
    assert.equal(await north.locator('[name=display_name]').inputValue(), 'Research <img src=x onerror=alert(1)>', 'polling preserves typing');
    await north.locator('button').filter({hasText: /^Save name$/}).click();
    await page.waitForFunction(() => document.querySelector('.subs-name')?.textContent.startsWith('Research <img'));
    assert.equal(await north.locator('.subs-name').textContent(), 'Research <img src=x onerror=alert(1)>');
    assert.equal(await page.locator('#set-subs img').count(), 0, 'display names are escaped');
    const [write] = await page.evaluate(() => writes);
    assert.deepEqual(write, {url:'/v2/subscriptions/name', body:{runner_id:'r1', profile:'engineering', display_name:'Research <img src=x onerror=alert(1)>'}});
    assert.equal(await page.locator('.subs-pc').nth(1).locator('.subs-name').textContent(), 'Engineering', 'another computer keeps its own label');
    await page.evaluate(() => subsBotPaint(document.querySelector('#rows'), 'builder'));
    assert.equal(await page.locator('#rows select').inputValue(), 'engineering', 'routing uses immutable key');
    assert.equal(await page.locator('#rows option:checked').textContent(), 'Research <img src=x onerror=alert(1)>');
    const manual = north.locator('[data-subs-weekly]');
    assert.equal(await manual.isVisible(), true, 'manual reporting shares the same disclosure');
    await manual.locator('[name=percent]').fill('42');
    await manual.getByRole('button', {name: 'Save report', exact: true}).click();
    await page.waitForFunction(() => document.querySelector('.subs-weekly')?.innerText.includes('42% used'));
    assert.deepEqual(await page.evaluate(() => writes.at(-1)), {url: '/v2/subscriptions/weekly', body: {
      runner_id: 'r1', profile: 'engineering', runtime: 'codex', used_percent: 42, resets_at: null}});
    await page.evaluate(() => { S.me.role = 'member'; S.me.id = 'viewer'; return renderSettingsSubs(true); });
    assert.equal(await page.locator('[data-subs-rename]').count(), 0, 'nonoperators cannot rename');
    assert.equal(await page.locator('.subs-controls').count(), 0, 'nonoperators get no manual write controls');
    assert.match(await north.locator('.subs-weekly').innerText(), /42% used/, 'usage remains visible');
    assert.deepEqual(errors, []);
    console.log('subscription identity and rename: ok');
    await page.close();
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
