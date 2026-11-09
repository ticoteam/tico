// Native window click wiring only: every command uses synthetic Tauri state, no server or bots.
import type {Browser, Page} from 'playwright';
const fs: typeof import('node:fs') = require('node:fs');
const path: typeof import('node:path') = require('node:path');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
// The preview's globals this script reads and sets: the mock preview's state, the synthetic Tauri bridge, and the
// calls the bridge records (declared here; the page defines them, and this file only names them).
type PreviewMeeting = {
  live: string; live_org: string; live_state: string; can_control: boolean; can_discard: boolean; paused_warning: boolean;
  discarded?: boolean; discard_pending?: boolean;
  actions: {items: {title: string; quote: string; owner: string; can_send: boolean; paused_warning: boolean}[]};
};
declare const window: Window & typeof globalThis & {
  __PREVIEW__: {state: {meetings: Record<string, PreviewMeeting>}};
  __TAURI__: {core: {invoke: (cmd: string, args?: Record<string, unknown>) => Promise<unknown>}};
  fixtureCalls: [string, Record<string, unknown>][];
};
declare const fixtureCalls: [string, Record<string, unknown>][];

async function recorderPause(browser: Browser): Promise<void> {
  const context = await browser.newContext();
  try {
    const page: Page = await context.newPage();
    const errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => route.request().url() === 'https://recorder.test/'
      ? route.fulfill({contentType: 'text/html', body: '<html><body><div id="app"></div></body></html>'})
      : route.abort());
    await page.goto('https://recorder.test/');
    await page.evaluate(() => { window.setInterval = (() => 0) as unknown as typeof window.setInterval; });
    await page.addScriptTag({content: fs.readFileSync(path.resolve(__dirname, '../../../recorder/ui/preview/mock.js'), 'utf8')});
    await page.evaluate(() => {
      const state = window.__PREVIEW__.state, meeting = state.meetings.m1!;
      Object.assign(meeting, {live: 'Team can join', live_org: 'team', live_state: 'live', can_control: true, can_discard: true, paused_warning: false});
      Object.assign(meeting.actions.items[0]!, {title: 'Paused launch date', quote: '', owner: 'bot:fixture', can_send: true, paused_warning: true});
      window.fixtureCalls = [];
      const original = window.__TAURI__.core.invoke;
      window.__TAURI__.core.invoke = async (cmd: string, args: Record<string, unknown> = {}): Promise<unknown> => {
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
    assert.equal(await page.evaluate(() => fixtureCalls.find(([cmd]) => cmd === 'send_item')![1].pausedConfirmed), true);
    // Finish: Save on a meeting with paused speech becomes Save anyway.
    await page.locator('.topbar [data-act="finish-open"]').click();
    await page.locator('#sheet.finish [data-act="finish-save"]').click();
    assert.equal(await page.evaluate(() => fixtureCalls.filter(([cmd]) => cmd === 'save_meeting').length), 0);
    await page.locator('#sheet.finish [data-act="finish-save"]').filter({hasText: 'Save anyway'}).click();
    assert.equal(await page.evaluate(() => fixtureCalls.find(([cmd]) => cmd === 'save_meeting')![1].pausedConfirmed), true);
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
}
export type Api = typeof recorderPause;
module.exports = recorderPause;
