// Native window click wiring only: every command uses synthetic Tauri state, no server or bots.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
module.exports = async browser => {
  const context = await browser.newContext();
  try {
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => route.request().url() === 'https://recorder.test/'
      ? route.fulfill({contentType: 'text/html', body: '<html><body><div id="app"></div></body></html>'})
      : route.abort());
    await page.goto('https://recorder.test/');
    await page.evaluate(() => { window.setInterval = () => 0; });
    await page.addScriptTag({content: fs.readFileSync(path.resolve(__dirname, '../../../recorder/ui/preview/mock.js'), 'utf8')});
    await page.evaluate(() => {
      const state = window.__PREVIEW__.state, meeting = state.meetings.m1;
      Object.assign(meeting, {live: 'Team can join', live_org: 'team', live_state: 'live', can_control: true, can_discard: true, paused_warning: false});
      Object.assign(meeting.actions.items[0], {title: 'Paused launch date', quote: '', owner: 'bot:fixture', can_send: true, paused_warning: true});
      window.fixtureCalls = [];
      const original = window.__TAURI__.core.invoke;
      window.__TAURI__.core.invoke = async (cmd, args = {}) => {
        window.fixtureCalls.push([cmd, args]);
        if (cmd === 'control_live') {
          Object.assign(meeting, {live_state: args.action === 'pause' ? 'paused' : 'live', paused_warning: true});
          meeting.actions.items.forEach(item => { item.can_send = meeting.live_state !== 'paused'; });
          return {};
        }
        if (cmd === 'keep_local') {
          Object.assign(meeting, {discarded: true, discard_pending: true, can_control: false, can_discard: false});
          meeting.actions.items.forEach(item => { item.can_send = false; });
          return {};
        }
        if (cmd === 'save_meeting' || cmd === 'send_item') return {};
        return original(cmd, args);
      };
    });
    await page.addScriptTag({content: fs.readFileSync(path.resolve(__dirname, '../../../recorder/ui/app/icons.js'), 'utf8')});
    await page.addScriptTag({content: fs.readFileSync(path.resolve(__dirname, '../../../recorder/ui/app/app.js'), 'utf8')});
    await page.evaluate(() => window.dispatchEvent(new Event('DOMContentLoaded')));
    await page.locator('.row[data-id="m1"]').click();
    const more = () => page.locator('[data-pop="more"]').click();
    const create = page.locator('[data-act="create-item"]').first();
    // Pause and Resume live in the More menu; the Share button shows the state.
    await more();
    await page.locator('[data-act="control-live"][data-action="pause"]').click();
    await page.locator('[data-act="share-open"]').filter({hasText: 'Paused'}).waitFor();
    assert.equal(await create.isDisabled(), true, 'Paused meetings cannot create tasks');
    await more();
    await page.locator('[data-act="control-live"][data-action="resume"]').click();
    await page.locator('[data-act="share-open"]').filter({hasText: 'Live'}).waitFor();
    // Create on a task that may quote paused speech asks once more.
    await create.click();
    assert.equal(await page.evaluate(() => fixtureCalls.filter(([cmd]) => cmd === 'send_item').length), 0);
    await page.getByRole('button', {name: 'Create anyway'}).click();
    assert.equal(await page.evaluate(() => fixtureCalls.find(([cmd]) => cmd === 'send_item')[1].pausedConfirmed), true);
    // Finish: Save on a meeting with paused speech becomes Save anyway.
    await page.locator('.topbar [data-act="finish-open"]').click();
    await page.locator('#sheet.finish [data-act="finish-save"]').click();
    assert.equal(await page.evaluate(() => fixtureCalls.filter(([cmd]) => cmd === 'save_meeting').length), 0);
    await page.locator('#sheet.finish [data-act="finish-save"]').filter({hasText: 'Save anyway'}).click();
    assert.equal(await page.evaluate(() => fixtureCalls.find(([cmd]) => cmd === 'save_meeting')[1].pausedConfirmed), true);
    // Discard live share asks first, then keeps the recording on this Mac.
    await more();
    await page.locator('[data-act="confirm-more"][data-confirm="discard"]').click();
    assert.equal(await page.evaluate(() => fixtureCalls.filter(([cmd]) => cmd === 'keep_local').length), 0);
    await page.locator('[data-act="keep-local"]').click();
    await page.getByText('Ending in Tico').waitFor();
    assert.equal(await create.isDisabled(), true);
    const controls = await page.evaluate(() => fixtureCalls.filter(([cmd]) => cmd === 'control_live').map(([, args]) => args.action));
    assert.deepEqual(controls, ['pause', 'resume']);
    assert.deepEqual(errors, []);
    console.log('Recorder Pause/Resume, paused Create and Save confirmations and Discard live share wiring passed (synthetic Tauri)');
  } finally { await context.close(); }
};
