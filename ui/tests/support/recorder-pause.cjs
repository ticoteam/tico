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
      Object.assign(meeting, {live_state: 'live', can_control: true, can_finish: true, can_discard: true, paused_warning: false});
      Object.assign(meeting.actions.items[0], {title: 'Paused launch date', quote: '', owner: 'bot:fixture', can_send: true, paused_warning: true});
      window.fixtureCalls = [];
      const original = window.__TAURI__.core.invoke;
      window.__TAURI__.core.invoke = async (cmd, args = {}) => {
        window.fixtureCalls.push([cmd, args]);
        if (cmd === 'control_live') {
          if (args.action === 'discard') Object.assign(meeting, {discarded: true, discard_pending: true, can_finish: false});
          else Object.assign(meeting, {live_state: args.action === 'pause' ? 'paused' : 'live', can_finish: args.action !== 'pause', paused_warning: true});
          meeting.actions.items.forEach(item => { item.can_send = meeting.live_state !== 'paused' && !meeting.discarded; item.paused_warning = true; });
          return {};
        }
        if (cmd === 'send_meeting' || cmd === 'send_item') return {};
        return original(cmd, args);
      };
    });
    await page.addScriptTag({content: fs.readFileSync(path.resolve(__dirname, '../../../recorder/ui/app/icons.js'), 'utf8')});
    await page.addScriptTag({content: fs.readFileSync(path.resolve(__dirname, '../../../recorder/ui/app/app.js'), 'utf8')});
    await page.evaluate(() => window.dispatchEvent(new Event('DOMContentLoaded')));
    await page.locator('.row[data-id="m1"]').click();
    await page.locator('[data-act="control-live"][data-action="pause"]').click();
    await page.locator('[data-act="control-live"][data-action="resume"]').waitFor();
    assert.equal(await page.locator('[data-act="finish"]').count(), 0);
    assert.equal(await page.locator('[data-act="send-item"]').first().isDisabled(), true);
    await page.locator('[data-act="control-live"][data-action="resume"]').click();
    await page.locator('[data-act="finish"]').waitFor();
    await page.locator('[data-act="send-item"]').first().click();
    assert.equal(await page.evaluate(() => fixtureCalls.filter(([cmd]) => cmd === 'send_item').length), 0);
    await page.locator('[data-act="confirm-item-send"]').click();
    assert.equal(await page.evaluate(() => fixtureCalls.find(([cmd]) => cmd === 'send_item')[1].pausedConfirmed), true);
    await page.locator('[data-pop="send"]').click();
    assert.equal(await page.locator('[data-act="send-meeting"]').count(), 0);
    await page.locator('[data-act="confirm-paused-send"]').click();
    await page.locator('[data-act="send-meeting"]').first().click();
    assert.equal(await page.evaluate(() => fixtureCalls.find(([cmd]) => cmd === 'send_meeting')[1].pausedConfirmed), true);
    await page.locator('[data-pop="discard"]').click();
    assert.equal(await page.evaluate(() => fixtureCalls.filter(([cmd, args]) => cmd === 'control_live' && args.action === 'discard').length), 0);
    await page.locator('[data-act="discard-live"]').click();
    await page.getByText('Discard pending · saved locally').waitFor();
    assert.equal(await page.locator('[data-act="send-item"]').first().isDisabled(), true);
    const controls = await page.evaluate(() => fixtureCalls.filter(([cmd]) => cmd === 'control_live').map(([, args]) => args.action));
    assert.deepEqual(controls, ['pause', 'resume', 'discard']);
    assert.deepEqual(errors, []);
    console.log('Recorder Pause/Resume, paused Send confirmation and pending Discard wiring passed (synthetic Tauri)');
  } finally { await context.close(); }
};
