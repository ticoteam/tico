// Real subscription controls (as the computer's pop-up shows them) with isolated fixture data: rename never changes
// routing keys, names are escaped, a manual weekly reading is entered as % left, and only an operator gets controls.
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
    await page.setContent('<main style="grid-column:1/-1;padding:16px"><div id="host"></div><div id="rows"><div class="sb-sub" data-bot-sub-line></div><div class="sb-row" data-bot-sub-row><span>Connection</span><select class="settings-inline-select"></select><span></span></div></div></main>');
    for (const name of ['tokens', 'base', 'components', 'settings']) {
      await page.addStyleTag({content: fs.readFileSync(path.join(__dirname, `../styles/${name}.css`), 'utf8')});
    }
    await page.addScriptTag({content: fs.readFileSync(path.join(__dirname, '../app/settings.js'), 'utf8')});
    await page.evaluate(harnesses => {
      window.$ = s => document.querySelector(s);
      window.esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
      window.ago = () => 'just now'; window.toast = () => {};
      window.formBusy = el => [...el.querySelectorAll('input')].some(f => f === document.activeElement || f.value !== f.defaultValue);
      window.S = {me: {id: 'ana', role: 'owner', cloud: true}, orgGroups: [], emps: [{name: 'builder', machine: {runner_id: 'r1', operator: 'ana'}}]};
      window.fixture = {profiles_by_computer: ['r1','r2'].map((runner_id, i) => ({runner_id, label: ['North', 'South'][i], profiles: [
        {name: 'engineering', id: 'subscription:' + JSON.stringify([runner_id, 'engineering']), display_name: 'Engineering', runtimes: {codex: {signed_in: true}}}]})),
        assignments: [{scope: 'bot', target: 'builder', profile: 'engineering'}]};
      window.writes = [];
      window.post = async (url, body) => { writes.push({url, body}); };
      window.get = async url => url === '/v2/models' ? {harnesses} : structuredClone(fixture);
      window.put = async (url, body) => {
        writes.push({url, body});
        const p = fixture.profiles_by_computer.find(c => c.runner_id === body.runner_id).profiles.find(p => p.name === body.profile);
        if (url === '/v2/subscriptions/name') { p.display_name = body.display_name.trim(); return {id: p.id, display_name: p.display_name}; }
        if (url === '/v2/subscriptions/weekly')
          p.runtimes[body.runtime].weekly = {used_percent: body.used_percent, resets_at: body.resets_at, reported_at: new Date().toISOString(), source: 'manual'};
        return {};
      };
      // Settings as the pop-up reads it: each computer's profiles; North's subscription and its controls drawn here.
      window.loadSettings = async () => {
        SETTINGS_DATA = {machines: fixture.profiles_by_computer.map(c => ({id: c.runner_id, label: c.label, operator: 'ana', profiles: structuredClone(c.profiles)}))};
        const host = $('#host'), c = {runner_id: 'r1', label: 'North'}, p = SETTINGS_DATA.machines[0].profiles[0], state = p.runtimes.codex;
        host.innerHTML = `<strong class="name">${esc(p.display_name)}</strong> <span class="left">${state.weekly ? `${100 - state.weekly.used_percent}% left` : 'No reading'}</span>`
          + subsRefreshHTML(c, p, 'codex', state, true) + subsControlsHTML(c, p, 'codex', state)
          // Rename is its own action on the line, as ui/app/computer-detail.js machineLineActionsHTML draws it.
          + (subsRenameHTML(c, p) && `<details class="computer-conn-rename"><summary>Rename</summary>${subsRenameHTML(c, p)}</details>`);
        window.drawn = (window.drawn || 0) + 1;
      };
    }, harnesses);
    await page.addScriptTag({content: fs.readFileSync(path.join(__dirname, '../app/subscriptions.js'), 'utf8')});
    await page.evaluate(async () => {
      await subsLoad(); await loadSettings();
      $('#host').onclick = ev => { subsHandleClick(ev); }; $('#host').onsubmit = ev => { subsHandleSubmit(ev); };
    });
    const host = page.locator('#host');
    assert.equal(await host.locator('details').count(), 2, 'Manage (the weekly reading) and Rename');
    assert.equal(await host.locator('[data-subs-rename]').isVisible(), false);
    assert.equal(await host.locator('[data-subs-refresh]').isVisible(), true);
    // Refresh from the keyboard, with its routing contract.
    await host.locator('[data-subs-refresh]').focus();
    await page.keyboard.press('Enter');
    await page.waitForFunction(() => window.drawn === 2);
    assert.deepEqual(await page.evaluate(() => writes.shift()), {url: '/v2/subscriptions/refresh', body: {
      runner_id: 'r1', profile: 'engineering', runtime: 'codex'}});
    await host.getByText('Rename', {exact: true}).click();
    await host.locator('[name=display_name]').fill('Research <img src=x onerror=alert(1)>');
    await host.locator('button').filter({hasText: /^Save name$/}).click();
    await page.waitForFunction(() => document.querySelector('#host .name')?.textContent.startsWith('Research <img'));
    assert.equal(await page.locator('#host img').count(), 0, 'display names are escaped');
    assert.deepEqual(await page.evaluate(() => writes.shift()), {url: '/v2/subscriptions/name',
      body: {runner_id: 'r1', profile: 'engineering', display_name: 'Research <img src=x onerror=alert(1)>'}});
    assert.equal(await page.evaluate(() => fixture.profiles_by_computer[1].profiles[0].display_name), 'Engineering', 'another computer keeps its own label');
    await page.evaluate(async () => { await subsLoad(); subsBotPaint(document.querySelector('#rows'), 'builder'); });   // as subsBotMount does
    assert.equal(await page.locator('#rows select').inputValue(), 'engineering', 'routing uses the immutable key');
    assert.equal(await page.locator('#rows option:checked').textContent(), 'Research <img src=x onerror=alert(1)>');
    assert.equal(await page.locator('#rows option').first().textContent(), "Computer's login", 'no choice means the computer\'s own login');
    // A manual reading is what the provider says is left; the server keeps what was used.
    await host.getByText('Manage', {exact: true}).click();
    const manual = host.locator('[data-subs-weekly]');
    assert.match(await manual.innerText(), /Left \(%\)/);
    await manual.locator('[name=percent]').fill('42');
    await manual.getByRole('button', {name: 'Save reading', exact: true}).click();
    await page.waitForFunction(() => document.querySelector('#host .left')?.textContent === '42% left');
    assert.deepEqual(await page.evaluate(() => writes.at(-1)), {url: '/v2/subscriptions/weekly', body: {
      runner_id: 'r1', profile: 'engineering', runtime: 'codex', used_percent: 58, resets_at: null}});
    assert.doesNotMatch(await host.innerText(), /used/i);
    await page.evaluate(() => { S.me.role = 'member'; S.me.id = 'viewer'; return loadSettings(); });
    assert.equal(await host.locator('[data-subs-rename], [data-subs-weekly], [data-subs-refresh], .subs-controls, .computer-conn-rename').count(), 0, 'nonoperators get no controls');
    assert.equal(await host.locator('.left').innerText(), '42% left', 'usage remains visible');
    assert.deepEqual(errors, []);
    console.log('subscription identity and rename: ok');
    await page.close();
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
